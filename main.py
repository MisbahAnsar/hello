import asyncio
import time
import json
import os
import random
from web3 import Web3
from dotenv import load_dotenv
from agents import MemeAgent
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn

load_dotenv()

RPC_URL = "https://testnet-rpc.monad.xyz/"
ARENA_ADDRESS = "0xA3ed093D1e3D632a13DC1389028A8fFF1264dADA"
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
STRATEGIES = ["Sniper", "Hodler", "Degen", "CopyTrader", "Whale"]

# Shared in-memory state -- market loop writes, API reads
live_state = {
    "phase": "WAITING",
    "roundEndTime": 0,
    "history": [],
}


# ---------- ABI loading ----------
def load_abi():
    abi_env = os.getenv("ARENA_ABI")
    if abi_env:
        try:
            return json.loads(abi_env)
        except Exception as e:
            print(f"Failed to parse ARENA_ABI env var: {e}")

    paths = [
        os.path.join(_APP_DIR, "arena_abi.json"),
        os.path.join(_APP_DIR, "..", "contracts", "artifacts", "contracts", "Arena.sol", "Arena.json"),
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p, "r") as f:
                    data = json.load(f)
                abi = data.get("abi", data)
                print(f"ABI loaded from {p}")
                return abi
            except Exception as e:
                print(f"Failed to load ABI from {p}: {e}")
    return None


# ---------- FastAPI ----------
@asynccontextmanager
async def lifespan(application):
    task = asyncio.create_task(blockchain_startup())
    yield
    task.cancel()

app = FastAPI(title="Meme Arena Backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
@app.get("/health")
def health():
    return {"status": "ok", "service": "meme-arena-backend"}


@app.get("/api/history")
@app.get("/history")
def get_history():
    return live_state


@app.get("/api/agents")
def get_agents():
    """Public agent addresses for the frontend."""
    path = os.path.join(_APP_DIR, "agent_public.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


# ---------- Blockchain background startup ----------
async def blockchain_startup():
    await asyncio.sleep(1)
    print("--- Blockchain init starting ---")

    arena_abi = load_abi()
    if arena_abi is None:
        print("WARNING: ABI not found. Market loop will not start.")
        return

    try:
        w3 = Web3(Web3.HTTPProvider(RPC_URL))
        if not w3.is_connected():
            print("Error: Could not connect to Monad Testnet")
            return

        arena_contract = w3.eth.contract(address=ARENA_ADDRESS, abi=arena_abi)

        admin_key = os.getenv("PRIVATE_KEY")
        if not admin_key:
            print("Error: PRIVATE_KEY not found in env")
            return

        admin_account = w3.eth.account.from_key(admin_key)
        print(f"Admin: {admin_account.address}")

        agents = create_agents(w3, arena_contract)
        await fund_agents(agents, w3, admin_account, admin_key)
        await register_agents(agents, w3, arena_contract, admin_account, admin_key)
        await market_loop(agents, w3, arena_contract, admin_account, admin_key)
    except Exception as e:
        print(f"Blockchain startup error: {e}")


# ---------- Agent helpers ----------
def create_agents(w3, arena_contract):
    agents = []
    print("Creating agents...")
    keys_file = os.path.join(_APP_DIR, "agent_keys.json")
    keys = {}
    if os.path.exists(keys_file):
        with open(keys_file, "r") as f:
            keys = json.load(f)

    for strategy in STRATEGIES:
        agent_name = f"Agent_{strategy}"
        if agent_name in keys:
            private_key = keys[agent_name]
        else:
            acc = w3.eth.account.create()
            private_key = acc.key.hex()
            keys[agent_name] = private_key
            with open(keys_file, "w") as f:
                json.dump(keys, f)

        agents.append(MemeAgent(agent_name, private_key, strategy, w3, arena_contract))
    return agents


async def register_agents(agents, w3, arena_contract, admin_account, admin_key):
    print("Registering agents...")
    nonce = w3.eth.get_transaction_count(admin_account.address)
    for agent in agents:
        try:
            if arena_contract.functions.isAgent(agent.address).call():
                print(f"Agent {agent.name} already registered.")
                continue
            print(f"Registering {agent.name}...")
            txn = arena_contract.functions.registerAgent(agent.address).build_transaction({
                "chainId": 10143, "gas": 150000,
                "gasPrice": int(w3.eth.gas_price * 1.2), "nonce": nonce,
            })
            signed = w3.eth.account.sign_transaction(txn, private_key=admin_key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            print(f"Registered {agent.name}: {w3.to_hex(tx_hash)}")
            nonce += 1
            time.sleep(1)
        except Exception as e:
            print(f"Failed to register {agent.name}: {e}")
    print("Registration complete.")


async def fund_agents(agents, w3, admin_account, admin_key):
    print("Checking agent balances...")
    nonce = w3.eth.get_transaction_count(admin_account.address)
    for agent in agents:
        try:
            balance = w3.eth.get_balance(agent.address)
            bal_mon = w3.from_wei(balance, "ether")
            if balance < w3.to_wei(0.005, "ether"):
                print(f"Agent {agent.name} low ({bal_mon} MON) - topping up...")
                tx = {
                    "nonce": nonce, "to": agent.address,
                    "value": w3.to_wei(0.02, "ether"), "gas": 21000,
                    "gasPrice": int(w3.eth.gas_price * 1.2), "chainId": 10143,
                }
                signed = w3.eth.account.sign_transaction(tx, admin_key)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                print(f"Sent 0.02 MON to {agent.name}: {w3.to_hex(tx_hash)}")
                nonce += 1
                time.sleep(1)
            else:
                print(f"Agent {agent.name}: {bal_mon} MON (OK)")
        except Exception as e:
            print(f"Failed to fund {agent.name}: {e}")


def update_state(phase, round_end_time, history):
    """Update the shared in-memory state that the API serves."""
    live_state["phase"] = phase
    live_state["roundEndTime"] = round_end_time
    live_state["history"] = list(history)

    # Also write to frontend/public/history.json for local dev
    local_path = os.path.join(_APP_DIR, "..", "frontend", "public", "history.json")
    if os.path.exists(os.path.dirname(local_path)):
        try:
            with open(local_path, "w") as f:
                json.dump(live_state, f)
        except Exception:
            pass


async def market_loop(agents, w3, arena_contract, admin_account, admin_key):
    print("Starting Market Loop...")
    history = []

    while True:
        print("--- STARTING NEW ROUND ---")
        await fund_agents(agents, w3, admin_account, admin_key)

        # Betting phase (30s)
        print(">>> BETTING PHASE (30s) <<<")
        try:
            nonce = w3.eth.get_transaction_count(admin_account.address)
            tx = arena_contract.functions.setBettingActive(True).build_transaction({
                "chainId": 10143, "gas": 80000,
                "gasPrice": int(w3.eth.gas_price * 1.2), "nonce": nonce,
            })
            signed = w3.eth.account.sign_transaction(tx, admin_key)
            w3.eth.send_raw_transaction(signed.raw_transaction)
            print("Betting Opened.")
        except Exception as e:
            print(f"Failed to open betting: {e}")

        betting_end = time.time() + 30
        while time.time() < betting_end:
            update_state("BETTING", betting_end, history)
            await asyncio.sleep(1)

        # Game phase (90s)
        print(">>> GAME PHASE (90s) <<<")
        try:
            nonce = w3.eth.get_transaction_count(admin_account.address)
            tx = arena_contract.functions.setBettingActive(False).build_transaction({
                "chainId": 10143, "gas": 80000,
                "gasPrice": int(w3.eth.gas_price * 1.2), "nonce": nonce,
            })
            signed = w3.eth.account.sign_transaction(tx, admin_key)
            w3.eth.send_raw_transaction(signed.raw_transaction)
            print("Betting Closed.")
        except Exception as e:
            print(f"Failed to close betting: {e}")

        start_time = time.time()
        round_duration = 90
        round_balances = {agent.name: 0 for agent in agents}

        while True:
            mock_price = random.uniform(0.1, 10.0)
            print(f"\n--- Market Price: ${mock_price:.2f} ---")

            tasks = []
            for agent in agents:
                decision, amount = agent.execute_strategy(mock_price)
                tasks.append(agent.trade(decision, amount))
                if decision == "buy":
                    round_balances[agent.name] += amount
                elif decision == "sell":
                    round_balances[agent.name] = max(0, round_balances[agent.name] - amount)

            await asyncio.gather(*tasks)

            entry = {
                "time": int(time.time()),
                "price": mock_price,
                "balances": dict(round_balances),
            }
            history.append(entry)
            if len(history) > 50:
                history.pop(0)

            update_state("ROUND", start_time + round_duration, history)

            if time.time() - start_time > round_duration:
                print("\n--- ROUND OVER ---")
                winner_agent = None
                highest = 0
                for agent in agents:
                    bal = round_balances[agent.name]
                    print(f"  {agent.name}: {bal} MEME")
                    if bal > highest:
                        highest = bal
                        winner_agent = agent

                if winner_agent:
                    print(f"Winner: {winner_agent.name} with {highest} MEME")
                    try:
                        nonce = w3.eth.get_transaction_count(admin_account.address)
                        tx = arena_contract.functions.endRound(winner_agent.address).build_transaction({
                            "chainId": 10143, "gas": 500000,
                            "gasPrice": int(w3.eth.gas_price * 1.2), "nonce": nonce,
                        })
                        signed = w3.eth.account.sign_transaction(tx, admin_key)
                        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                        print(f"Round ended: {w3.to_hex(tx_hash)}")
                    except Exception as e:
                        print(f"Failed to end round: {e}")
                break

            await asyncio.sleep(2)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

import asyncio
import time
import json
import os
import random
from web3 import Web3
from dotenv import load_dotenv
from agents import MemeAgent

# Load environment variables
load_dotenv(dotenv_path="../contracts/.env") # Try to load from contracts .env if exists, or local

# Configuration
RPC_URL = "https://testnet-rpc.monad.xyz/"
ARENA_ADDRESS = "0xa970eb753d93217Fc12687225889121494EFd41A" # Deployed Address (v3 - auto-payout + auto-refund)

# Read ABI
try:
    with open("../contracts/artifacts/contracts/Arena.sol/Arena.json", "r") as f:
        contract_json = json.load(f)
        ARENA_ABI = contract_json["abi"]
except FileNotFoundError:
    print("Error: ABI file not found. Make sure you complied the contracts.")
    exit(1)

# Initialize Web3
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    print("Error: Could not connect to Monad Testnet")
    exit(1)

arena_contract = w3.eth.contract(address=ARENA_ADDRESS, abi=ARENA_ABI)

# Strategy Definitions
STRATEGIES = ["Sniper", "Hodler", "Degen", "CopyTrader", "Whale"]

# Generate or Load Agents
# For MVP, we use the same PRIVATE_KEY from .env for all agents? 
# NO, that would cause nonce issues if they run concurrently.
# We should generate random accounts for them or mock them.
# BUT the contract requires them to be whitelisted ("registerAgent").
# So we must register them first. 
# SIMPLIFICATION: We will generate 5 random wallets, and use the MAIN DEPLOYER to register them.

ADMIN_PRIVATE_KEY = os.getenv("PRIVATE_KEY")
if not ADMIN_PRIVATE_KEY:
    print("Error: PRIVATE_KEY not found in env")
    exit(1)

admin_account = w3.eth.account.from_key(ADMIN_PRIVATE_KEY)

async def register_agents(agents):
    print("Registering agents...")
    nonce = w3.eth.get_transaction_count(admin_account.address)
    for agent in agents:
        # Check if already registered (mock check or call contract)
        is_registered = arena_contract.functions.isAgent(agent.address).call()
        if is_registered:
            print(f"Agent {agent.name} already registered.")
            continue

        print(f"Registering {agent.name}...")
        txn = arena_contract.functions.registerAgent(agent.address).build_transaction({
            'chainId': 10143,
            'gas': 150000,
            'gasPrice': int(w3.eth.gas_price * 1.2),
            'nonce': nonce
        })
        signed_txn = w3.eth.account.sign_transaction(txn, private_key=ADMIN_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
        print(f"Registration tx sent for {agent.name}: {w3.to_hex(tx_hash)}")
        nonce += 1
        time.sleep(1) # Avoid rate limits or nonce issues

    print("Registration complete.")

def create_agents():
    agents = []
    print("Creating agents...")
    for i, strategy in enumerate(STRATEGIES):
        # Generate a new account
        # In production, load from a file to persist. 
        # Here we just generate new ones each run? That requires re-registration.
        # Better: Save keys to a file `agent_keys.json`
        
        agent_name = f"Agent_{strategy}"
        
        # Checking for existing keys
        keys_file = "agent_keys.json"
        if os.path.exists(keys_file):
            with open(keys_file, 'r') as f:
                keys = json.load(f)
        else:
            keys = {}

        if agent_name in keys:
            private_key = keys[agent_name]
        else:
            acc = w3.eth.account.create()
            private_key = acc.key.hex()
            keys[agent_name] = private_key
            with open(keys_file, 'w') as f:
                json.dump(keys, f)
        
        agent = MemeAgent(agent_name, private_key, strategy, w3, arena_contract)
        agents.append(agent)
    return agents

async def market_loop(agents):
    print("Starting Market Loop...")
    history = []
    HISTORY_FILE = "../frontend/public/history.json"
    start_time = time.time()

    while True: # Outer Loop for Rounds
        print("--- STARTING NEW ROUND ---")
        
        # Auto-refund agents if their balance is low
        await fund_agents(agents)
        
        # 1. Open Betting Phase (30s)
        print(">>> BETTING PHASE (30s) <<<")
        try:
            nonce = w3.eth.get_transaction_count(admin_account.address)
            tx = arena_contract.functions.setBettingActive(True).build_transaction({
                'chainId': 10143,
                'gas': 80000,
                'gasPrice': int(w3.eth.gas_price * 1.2),
                'nonce': nonce
            })
            signed_tx = w3.eth.account.sign_transaction(tx, ADMIN_PRIVATE_KEY)
            w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            print("Betting Opened.")
        except Exception as e:
            print(f"Failed to open betting: {e}")

        # Betting Phase Loop (Write to history so frontend knows)
        betting_end = time.time() + 30
        while time.time() < betting_end:
            timestamp = int(time.time())
            # Write "BETTING" phase
            data = {
                "phase": "BETTING",
                "roundEndTime": betting_end,
                "history": history 
            }
            # We need to preserve history struct
            with open(HISTORY_FILE, "w") as f:
                json.dump(data, f)
            time.sleep(1)

        # 2. Close Betting & Start Game Phase
        print(">>> GAME PHASE (90s) <<<")
        try:
            nonce = w3.eth.get_transaction_count(admin_account.address)
            tx = arena_contract.functions.setBettingActive(False).build_transaction({
                'chainId': 10143,
                'gas': 80000,
                'gasPrice': int(w3.eth.gas_price * 1.2),
                'nonce': nonce
            })
            signed_tx = w3.eth.account.sign_transaction(tx, ADMIN_PRIVATE_KEY)
            w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            print("Betting Closed.")
        except Exception as e:
            print(f"Failed to close betting: {e}")

        start_time = time.time()
        round_duration = 90 # Total 120s round (30s bet + 90s game)
        
        # Per-round balance tracking (reset each round for fair competition)
        round_balances = {agent.name: 0 for agent in agents}
        
        while True: # Inner Market Loop
            # 1. Mock Price Update
            mock_price = random.uniform(0.1, 10.0)
            print(f"\n--- Market Price: ${mock_price:.2f} ---")

            # 2. Agents Decide
            tasks = []
            for agent in agents:
                decision, amount = agent.execute_strategy(mock_price)
                tasks.append(agent.trade(decision, amount))
                # Track per-round balances locally
                if decision == "buy":
                    round_balances[agent.name] += amount
                elif decision == "sell":
                    round_balances[agent.name] = max(0, round_balances[agent.name] - amount)

            await asyncio.gather(*tasks)

            # 3. Update History
            timestamp = int(time.time())
            agent_data = {name: bal for name, bal in round_balances.items()}
            entry = {
                "time": timestamp,
                "price": mock_price,
                "balances": agent_data
            }
            history.append(entry)
            if len(history) > 50:
                history.pop(0)
            
            # Write to file with PHASE="ROUND"
            data = {
                "phase": "ROUND",
                "roundEndTime": start_time + round_duration,
                "history": history
            }
            with open(HISTORY_FILE, "w") as f:
                json.dump(data, f)

            # 4. Check for Round End
            elapsed_time = time.time() - start_time
            if elapsed_time > round_duration:
                print("\n--- ROUND OVER ---")
                # Determine Winner from this round's balances
                highest_balance = 0
                winner_agent = None
                for agent in agents:
                    bal = round_balances[agent.name]
                    print(f"  {agent.name}: {bal} MEME")
                    if bal > highest_balance:
                        highest_balance = bal
                        winner_agent = agent
                
                if winner_agent:
                    print(f"Winner is {winner_agent.name} with {highest_balance} MEME")
                    try:
                        nonce = w3.eth.get_transaction_count(admin_account.address)
                        tx = arena_contract.functions.endRound(winner_agent.address).build_transaction({
                            'chainId': 10143,
                            'gas': 500000,
                            'gasPrice': int(w3.eth.gas_price * 1.2),
                            'nonce': nonce
                        })
                        signed_tx = w3.eth.account.sign_transaction(tx, ADMIN_PRIVATE_KEY)
                        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
                        print(f"Ended Round! Winners auto-paid, lost bets swept to admin. Tx: {w3.to_hex(tx_hash)}")
                    except Exception as e:
                        print(f"Failed to end round: {e}")
                
                break # Exit inner loop, start new round

            # 5. Wait
            await asyncio.sleep(2)


    
async def fund_agents(agents):
    print("Checking agent balances...")
    nonce = w3.eth.get_transaction_count(admin_account.address)
    funded_count = 0
    for agent in agents:
        balance = w3.eth.get_balance(agent.address)
        bal_mon = w3.from_wei(balance, 'ether')
        
        if balance < w3.to_wei(0.005, 'ether'):
            print(f"Agent {agent.name} low ({bal_mon} MON) - topping up with 0.02 MON...")
            tx = {
                'nonce': nonce,
                'to': agent.address,
                'value': w3.to_wei(0.02, 'ether'),
                'gas': 21000,
                'gasPrice': int(w3.eth.gas_price * 1.2),
                'chainId': 10143
            }
            signed_tx = w3.eth.account.sign_transaction(tx, ADMIN_PRIVATE_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            print(f"Sent 0.02 MON to {agent.name}: {w3.to_hex(tx_hash)}")
            nonce += 1
            funded_count += 1
            time.sleep(1)
        else:
            print(f"Agent {agent.name}: {bal_mon} MON (OK)")
    if funded_count > 0:
        print(f"Funded {funded_count} agents.")
    else:
        print("All agents have sufficient balance.")

async def main():
    agents = create_agents()
    
    # Funding
    await fund_agents(agents)

    
    # We need to register them on-chain using the Admin key
    # Only do this if they are not allowed to trade.
    await register_agents(agents)
    
    # Start the loop
    await market_loop(agents)

if __name__ == "__main__":
    asyncio.run(main())

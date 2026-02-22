import json
import os
from web3 import Web3

STRATEGIES = ["Sniper", "Hodler", "Degen", "CopyTrader", "Whale"]
KEY_FILE = "agent_keys.json"
PUBLIC_FILE = "agent_public.json"

w3 = Web3()

agent_keys = {}
agent_public = {}

print("Genering wallets for agents...")

for strategy in STRATEGIES:
    agent_name = f"Agent_{strategy}"
    acc = w3.eth.account.create()
    agent_keys[agent_name] = acc.key.hex()
    agent_public[agent_name] = acc.address
    print(f"{agent_name}: {acc.address}")

with open(KEY_FILE, "w") as f:
    json.dump(agent_keys, f, indent=4)

with open(PUBLIC_FILE, "w") as f:
    json.dump(agent_public, f, indent=4)

print(f"Saved private keys to {KEY_FILE} and public addresses to {PUBLIC_FILE}")

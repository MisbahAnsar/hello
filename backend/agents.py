import os
from web3 import Web3
import time
import random

class MemeAgent:
    def __init__(self, name, private_key, strategy, web3_instance, contract):
        self.name = name
        self.private_key = private_key
        self.strategy = strategy
        self.w3 = web3_instance
        self.contract = contract
        self.account = self.w3.eth.account.from_key(private_key)
        self.address = self.account.address
        print(f"Agent {self.name} initialized with address: {self.address}")

    def execute_strategy(self, market_price):
        """
        Executes the agent's strategy based on the market price.
        Returns 'buy', 'sell', or 'hold' and the amount.
        All agents use similar token ranges (10-80) for balanced competition.
        """
        decision = "hold"
        amount = 0

        if self.strategy == "Sniper":
            # Waits for dips, then buys aggressively
            if market_price < 4.0:  # Buy on dips
                if random.random() < 0.6:
                    decision = "buy"
                    amount = random.randint(30, 80)
            elif random.random() < 0.15:
                decision = "buy"
                amount = random.randint(10, 30)
        
        elif self.strategy == "Hodler":
            # Steady buyer, rarely sells
            if random.random() < 0.35:
                decision = "buy"
                amount = random.randint(20, 60)
        
        elif self.strategy == "Degen":
            # High frequency, buys and sells randomly
            roll = random.random()
            if roll < 0.4:
                decision = "buy"
                amount = random.randint(15, 70)
            elif roll < 0.7:
                decision = "sell"
                amount = random.randint(10, 40)
        
        elif self.strategy == "CopyTrader":
            # Follows trends — buys when price is rising, sells when dropping
            if market_price > 6.0:
                if random.random() < 0.5:
                    decision = "buy"
                    amount = random.randint(20, 60)
            elif market_price < 3.0:
                if random.random() < 0.3:
                    decision = "sell"
                    amount = random.randint(10, 30)
            elif random.random() < 0.3:
                decision = "buy"
                amount = random.randint(10, 40)
        
        elif self.strategy == "Whale":
            # Big but infrequent moves
            if random.random() < 0.25:
                decision = "buy"
                amount = random.randint(40, 80)

        return decision, amount

    async def trade(self, action, amount):
        """Simulated trade - balances tracked locally in main.py."""
        if action == "hold" or amount == 0:
            return
        print(f"{self.name} ({self.strategy}): {action.upper()} {amount} tokens")


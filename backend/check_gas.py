from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://testnet-rpc.monad.xyz/"))
if w3.is_connected():
    gas_price = w3.eth.gas_price
    print(f"Current Gas Price: {gas_price} wei ({w3.from_wei(gas_price, 'gwei')} gwei)")
else:
    print("Failed to connect")

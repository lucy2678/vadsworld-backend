import json
from web3 import Web3

BSC_RPC_URL = "https://bsc-dataseed.binance.org/"
CONTRACT_ADDRESS = "0xeb85d16502bd603749fA8774d0d4717e324e0850"

CONTRACT_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "tokenId", "type": "uint256"},
            {"indexed": False, "internalType": "int256", "name": "x", "type": "int256"},
            {"indexed": False, "internalType": "int256", "name": "y", "type": "int256"},
            {"indexed": False, "internalType": "string", "name": "country", "type": "string"}
        ],
        "name": "LandMinted",
        "type": "event"
    }
]

w3 = Web3(Web3.HTTPProvider(BSC_RPC_URL))
contract = w3.eth.contract(address=Web3.to_checksum_address(CONTRACT_ADDRESS), abi=CONTRACT_ABI)

# We want to find the coordinates for token 151 and 152
# Since we don't know the block, we can just fetch all LandMinted events
# But wait, fetching all events might fail.
# Let's try fetching in chunks of 5000 from 90744785 to latest
latest_block = w3.eth.block_number
print(f"Latest block: {latest_block}")

START_BLOCK = 90744785
CHUNK_SIZE = 4999

current_block = START_BLOCK
found_tokens = {}

while current_block <= latest_block:
    end_block = min(current_block + CHUNK_SIZE, latest_block)
    try:
        events = contract.events.LandMinted.get_logs(from_block=current_block, to_block=end_block)
        for event in events:
            token_id = event['args']['tokenId']
            if token_id in [151, 152]:
                x = event['args']['x']
                y = event['args']['y']
                lng = x / 100000.0
                lat = y / 100000.0
                string_id = f"{lng:.5f}_{lat:.5f}"
                found_tokens[token_id] = string_id
                print(f"Found token {token_id}: {string_id}")
                
        if len(found_tokens) == 2:
            break
    except Exception as e:
        print(f"Error at {current_block}-{end_block}: {e}")
        # If error, try smaller chunk
        pass
    
    current_block = end_block + 1

print("Final found:", found_tokens)

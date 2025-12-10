#!/usr/bin/env python3
"""Simple test for Pinecone connection."""
import httpx
import json
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("PINECONE_API_KEY")
host = os.getenv("PINECONE_INDEX_HOST")

print(f"Testing with:")
print(f"  Host: {host}")
print(f"  API Key: {api_key[:15]}...")

# Test a simple query to check connection
url = f"https://{host}/describe_index_stats"

headers = {
    "Api-Key": api_key,
    "Content-Type": "application/json"
}

payload = {}

try:
    response = httpx.post(url, json=payload, headers=headers)
    print(f"\nResponse status: {response.status_code}")
    print(f"Response: {response.text[:500]}")
except Exception as e:
    print(f"\nError: {e}")
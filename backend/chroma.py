import chromadb

client = chromadb.PersistentClient(path="/app/chroma_data")
collection = client.get_or_create_collection("packages")

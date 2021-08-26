from pymongo import MongoClient

client = MongoClient("mongodb+srv://valstan:nitro2000@postopus.qjxr9.mongodb.net/postopus?retryWrites=true&w=majority")

db = client["postopus"]

collection = db["mi"]

list_name_docs = ("all_bezfoto", "bezfoto", "krugozor", "music", "novost", "prikol", "reklama", "repost_aprel",
                  "repost_brigadir", "repost_krugozor", "repost_reklama", "repost_valstan")

list_result = []

for i in list_name_docs:
    list_result.append({"title": i, "lip": ["0"], "hash": ["0"], "list": ["0"]})

ins_result = collection.insert_many(list_result)

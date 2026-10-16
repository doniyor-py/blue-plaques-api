import bcrypt
from pymongo import MongoClient

users = MongoClient("mongodb://localhost:27017").plaquesDB.users
users.delete_many({})                              
users.create_index("username", unique=True)      


def make_user(username, password, admin=False):
    return {
        "username": username,
        "email": username + "@example.com",
        "password": bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()),
        "admin": admin,
    }


accounts = [make_user("admin1", "AdminPass123", admin=True)]
accounts += [make_user("user" + str(i), "UserPass123") for i in range(10)]
users.insert_many(accounts)
print("Created", len(accounts), "users: admin1 (admin) and user0-user9")

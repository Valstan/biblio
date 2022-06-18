from bin.rw.get_mongo_base import get_mongo_base
from config import session


def old_get_session(name_base, name_session):
    collection = get_mongo_base(session)['config']
    session.update(collection.find_one({'title': 'config'}))
    collection = get_mongo_base(session)[name_base]
    session.update(collection.find_one({'title': 'config'}))
    session.update({"name_session": name_session})

    return session

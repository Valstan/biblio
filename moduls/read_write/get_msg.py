def get_msg(vkapp, group=-158787639, offset=0, count=1):
    return vkapp.wall.get(owner_id=group, count=count, offset=offset, v=5.102)['items']


if __name__ == '__main__':
    pass

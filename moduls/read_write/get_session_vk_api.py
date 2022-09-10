from vk_api import VkApi


def get_session_vk_api(token):
    vk_session = VkApi(token=token)
    return vk_session.get_api()


if __name__ == '__main__':
    pass

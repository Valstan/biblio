from vk_api import VkApi


def get_session_vk_api(lp):
    vk_session = VkApi(lp[0], lp[1])
    vk_session.auth()
    return vk_session.get_api()


if __name__ == '__main__':
    pass

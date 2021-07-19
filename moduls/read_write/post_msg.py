def post_msg(vk, group, text_send, attach):
    vk.wall.post(owner_id=group,
                 from_group=1,
                 message=text_send,
                 attachments=attach,
                 v=5.102)


if __name__ == '__main__':
    pass

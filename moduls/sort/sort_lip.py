def sort_lip(msg, lip):
    skleika = str(msg['owner_id']) + str(msg['id'])
    if skleika in lip:
        msg = ''
    return msg, skleika

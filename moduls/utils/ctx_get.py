import ssl


def ctx_get():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


if __name__ == '__main__':
    pass

from bases.logpass import prefix_lp


def change_lp(prefix):
    for i in prefix_lp:
        for y in i[0]:
            if prefix == y:
                return i[1], i[2]


if __name__ == '__main__':
    pass

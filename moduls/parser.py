from moduls.sort.sort_old_date import sort_old_date
from moduls.utils.avtortut import avtortut
from moduls.utils.clear_copy_history import clear_copy_history
from moduls.utils.correct_txt import correct_txt
from moduls.read_write.read_groups_posts import readposts
from moduls.sort.sort_black_list import sort_black_list
from moduls.sort.sort_double import sort_double
from moduls.sort.sort_lip import sort_lip
from moduls.sort.sort_po_foto import sort_po_foto
from moduls.sort.sort_sfoto_bezfoto import sort_sfoto_bezfoto
from moduls.sort.sort_views_bezfoto import sort_views_bezfoto


def parser(vkapp, base, name_novost):
    new_posts = readposts(vkapp, base, name_novost, 20)
    oldposts_maingroup = readposts(vkapp, base, 'post_group', 100)
    maingroup_msg_list = []
    for sample in oldposts_maingroup:
        maingroup_msg_list.append(clear_copy_history(sample))
    news_msg_list = []
    for sample in new_posts:
        if not sort_old_date(sample):
            continue
        sample = clear_copy_history(sample)
        sample, skleika = sort_lip(sample, base['lip'])
        if not sample: continue
        if sort_black_list(sample['text']):
            continue
        sample = correct_txt(sample)
        sample = sort_views_bezfoto(sample)
        sample, base = sort_sfoto_bezfoto(sample, base)
        if not sample:
            base['lip'].append(skleika)
            continue
        sample, histo = sort_po_foto(sample, base)
        if not sample: continue
        sample['text'] = ''.join(map(str, (base['heshteg'][name_novost], avtortut(sample))))
        sample = sort_double(sample, news_msg_list, maingroup_msg_list, base)
        if not sample: continue
        news_msg_list.append(sample)
    news_msg_list.sort(key=lambda x: x['views']['count'], reverse=True)
    return base, news_msg_list

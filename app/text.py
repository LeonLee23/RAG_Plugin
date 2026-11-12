import re
import unicodedata

import jieba

STOP = {"的", "了", "是", "在", "和", "与", "什么", "怎么", "如何", "请问", "这个", "那个"}


def normalize(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def terms(text, *, query=False):
    words = [w.strip() for w in jieba.lcut(normalize(text)) if re.search(r'\w', w)]
    if query:
        words = list(dict.fromkeys(w for w in words if w not in STOP))[:12]
    return words


def token_text(text):
    return ' '.join(terms(text))

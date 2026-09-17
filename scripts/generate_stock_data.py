# -*- coding: utf-8 -*-
"""从 server/metaphysics/stock_element.py 导出五行数据表为 JS，
避免手动转录汉字出错。重新生成：python scripts/generate_stock_data.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from metaphysics import stock_element as se  # noqa: E402


def j(s):
    return json.dumps(s, ensure_ascii=False)


out = []
out.append('/**')
out.append(' * 汉字五行 / 行业 / 关键词数据表。')
out.append(' * 由 scripts/generate_stock_data.py 从 Python 源自动导出，')
out.append(' * 杜绝手动转录汉字出错。重新生成：python scripts/generate_stock_data.py')
out.append(' */')
out.append('')
out.append('// 汉字 -> 五行')
out.append('export const CHAR_ELEMENT = {')
for ch, e in se.CHAR_ELEMENT.items():
    out.append('  ' + j(ch) + ': ' + j(e) + ',')
out.append('}')
out.append('')
out.append('// 行业 -> 五行')
out.append('export const INDUSTRY_ELEMENT = {')
for k, v in se.INDUSTRY_ELEMENT.items():
    out.append('  ' + j(k) + ': ' + j(v) + ',')
out.append('}')
out.append('')
out.append('// 名称关键词 -> 五行')
out.append('export const NAME_KEYWORD_ELEMENT = {')
for k, v in se.NAME_KEYWORD_ELEMENT.items():
    out.append('  ' + j(k) + ': ' + j(v) + ',')
out.append('}')
out.append('')

target = Path(__file__).resolve().parent.parent / 'src' / 'lib' / 'metaphysics' / 'stockElementData.js'
target.write_text('\n'.join(out), encoding='utf-8')
print('生成完成: %s' % target)
print('字表 %d / 行业 %d / 关键词 %d' % (
    len(se.CHAR_ELEMENT), len(se.INDUSTRY_ELEMENT), len(se.NAME_KEYWORD_ELEMENT)))

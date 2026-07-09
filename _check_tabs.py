import re

src = open(r'd:\BI\bilibili-monitor\src\gui\main_window.py', encoding='utf-8').read()
old = open(r'd:\BI\bilibili-monitor-dist-old\_internal\src\gui\main_window.py', encoding='utf-8').read()
for name, content in [('src', src), ('old', old)]:
    print(f'--- {name} ---')
    for line in content.splitlines():
        if 'addTab' in line:
            print(line.strip())

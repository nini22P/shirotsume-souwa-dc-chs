import argparse, csv, re, sys
from collections import defaultdict
from pathlib import Path

TAG_RE = re.compile(r'<[^>]*>')

def collect(path: Path, col_text: str, col_trans: str):
    cnt_trans = defaultdict(int)
    cnt_total = defaultdict(int)
    examples = defaultdict(list)

    with path.open('r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    for i, row in enumerate(rows, start=2):
        text = TAG_RE.sub('', (row.get(col_text) or '').strip())
        trans = TAG_RE.sub('', (row.get(col_trans) or '').strip())

        for ch in text:
            cnt_total[ch] += 1

        for ch in trans:
            cnt_trans[ch] += 1
            cnt_total[ch] += 1

        if trans:
            seen = set()
            for ch in trans:
                if ch not in seen:
                    seen.add(ch)
                    examples[ch].append((f'{path.name}:{i}', text, trans))

    return cnt_trans, cnt_total, examples


def main():
    parser = argparse.ArgumentParser(description='Character frequency analysis')
    parser.add_argument('-s', '--scr-csv', default='scr.csv', help='Input scr CSV (default: scr.csv)')
    parser.add_argument('-b', '--bin-csv', default='bin.csv', help='Input bin CSV (default: bin.csv)')
    parser.add_argument('-o', '--output', default='char_analysis.txt', help='Output analysis text file (default: char_analysis.txt)')
    args = parser.parse_args()

    csv_path = Path(args.scr_csv)
    bin_csv_path = Path(args.bin_csv)
    out_path = Path(args.output)

    s_cnt, s_total, s_ex = collect(csv_path, 'text', 'translation')
    b_cnt, b_total, b_ex = collect(bin_csv_path, 'text', 'replace')

    cnt_trans = s_cnt
    for ch, n in b_cnt.items():
        cnt_trans[ch] += n

    cnt_total = s_total
    for ch, n in b_total.items():
        cnt_total[ch] += n

    examples = s_ex
    for ch, lst in b_ex.items():
        examples[ch].extend(lst)

    chars = [(ch, cnt_trans[ch], cnt_total[ch]) for ch in cnt_trans]
    chars.sort(key=lambda x: (x[1], x[0]))

    report = []
    report.append('=' * 80)
    report.append('字符使用频率分析报告')
    report.append(f'统计范围: scr.csv(translation) + bin.csv(replace)')
    report.append(f'不重复字符总数: {len(chars)}')
    report.append(f'翻译中字符总出现次数: {sum(cnt_trans.values())}')
    report.append('=' * 80)
    report.append('')

    bands = [(1, '仅出现1次'), (2, '出现2次'), (3, '出现3次'),
             (10, '出现4-10次'), (50, '出现11-50次'),
             (100, '出现51-100次'), (float('inf'), '100次以上')]
    prev = 0
    report.append('--- 频率分布 ---')
    for limit, label in bands:
        count = sum(1 for _, c, _ in chars if prev < c <= limit)
        total = sum(c for _, c, _ in chars if prev < c <= limit)
        if count:
            report.append(f'  {label}: {count} 个字符, 共出现 {total} 次')
        prev = limit
    report.append('')

    report.append('--- 详细列表 ---')
    report.append(f'{"字符":<6} {"Unicode":<10} {"翻译次数":<10} {"总次数":<8} {"例句(翻译行)"}')
    report.append('-' * 120)

    for ch, n, total in chars:
        ex_list = examples.get(ch, [])
        ex_strs = []
        for src, orig, trans in ex_list[:3]:
            excerpt = f'[{src}] {orig[:50]} → {trans[:60]}'
            ex_strs.append(excerpt)
        ex_str = ' | '.join(ex_strs)
        if len(ex_list) > 3:
            ex_str += f' ... (共{len(ex_list)}处)'
        report.append(f'{ch:<6} U+{ord(ch):04X}  {n:<10} {total:<8} {ex_str}')

    out_path.write_text('\n'.join(report), encoding='utf-8')
    print(f'Wrote {out_path} ({len(chars)} chars, {len(report)} lines)')


if __name__ == '__main__':
    sys.exit(main())

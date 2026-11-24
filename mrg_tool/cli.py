import argparse
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(prog='mrg_tool', description='Shirotsume Souwa MRG toolkit')
    sub = p.add_subparsers(dest='tool', required=True)

    # img
    p_img = sub.add_parser('img', help='MRG image container tools')
    img_sub = p_img.add_subparsers(dest='command', required=True)
    p_img_u = img_sub.add_parser('unpack', help='Extract images to PNG')
    p_img_u.add_argument('-i', '--input', required=True, help='Source .mrg file')
    p_img_u.add_argument('-o', '--output', required=True, help='Output directory')
    p_img_u.add_argument(
        '--no-compose',
        action='store_true',
        help='Keep 0x43 layers without composing onto 0x41',
    )
    p_img_r = img_sub.add_parser('replace', help='Replace images in MRG with PNGs')
    p_img_r.add_argument('-i', '--input', required=True, help='Original .mrg file')
    p_img_r.add_argument('-d', '--dir', required=True, help='PNG replace directory')
    p_img_r.add_argument('-o', '--output', required=True, help='Output .mrg file')
    p_img_r.add_argument('--dry-run', action='store_true', help='Preview only, no file output')

    # scr
    p_scr = sub.add_parser('scr', help='SCR.MRG script container tools')
    scr_sub = p_scr.add_subparsers(dest='command', required=True)
    p_scr_u = scr_sub.add_parser('unpack', help='Extract scripts from SCR.MRG')
    p_scr_u.add_argument('-i', '--input', required=True, help='Input .mrg file')
    p_scr_u.add_argument('-o', '--output', default='_unpacked', help='Output script directory')
    p_scr_r = scr_sub.add_parser('repack', help='Repack scripts to SCR.MRG')
    p_scr_r.add_argument('-i', '--input', default='_unpacked', help='Input script directory')
    p_scr_r.add_argument('-o', '--output', required=True, help='Output .mrg file')

    # scradr
    p_scradr = sub.add_parser('scradr', help='SCRADR.MRG offset table tools')
    scradr_sub = p_scradr.add_subparsers(dest='command', required=True)
    p_scradr_rb = scradr_sub.add_parser('rebuild', help='Rebuild SCRADR.MRG with updated label offsets')
    p_scradr_rb.add_argument('-i', '--input', required=True, help='Input SCRADR.MRG')
    p_scradr_rb.add_argument('-o', '--output', required=True, help='Output SCRADR.MRG')

    # syspvr
    p_pvr = sub.add_parser('syspvr', help='SYSPVR.MRG font texture tools')
    pvr_sub = p_pvr.add_subparsers(dest='command', required=True)
    p_pvr_u = pvr_sub.add_parser('unpack', help='Unpack MRG to PNGs')
    p_pvr_u.add_argument('-i', '--input', required=True, help='Input .mrg file')
    p_pvr_u.add_argument('-o', '--output', required=True, help='Output directory with PNGs + _meta.csv')
    p_pvr_p = pvr_sub.add_parser('pack', help='Pack PNGs to MRG')
    p_pvr_p.add_argument('-i', '--input', required=True, help='Input directory with PNGs + _meta.csv')
    p_pvr_p.add_argument('-o', '--output', required=True, help='Output .mrg file')

    args = p.parse_args()

    # 按子命令延迟导入，避免跑 scr/scradr 时加载 numpy/numba
    match (args.tool, args.command):
        case ('img', 'unpack'):
            from .img import unpack
            unpack(args.input, args.output, compose=not args.no_compose)
        case ('img', 'replace'):
            from .img import replace
            replace(args.input, args.dir, args.output, dry_run=args.dry_run)
        case ('scr', 'unpack'):
            from .scr import unpack
            unpack(args.input, args.output)
        case ('scr', 'repack'):
            from .scr import repack
            repack(args.input, args.output)
        case ('scradr', 'rebuild'):
            from .scradr import rebuild
            rebuild(args.input, args.output)
        case ('syspvr', 'unpack'):
            from .syspvr import unpack
            unpack(Path(args.input), Path(args.output))
        case ('syspvr', 'pack'):
            from .syspvr import pack
            pack(Path(args.input), Path(args.output))
        case _:
            p.print_help()

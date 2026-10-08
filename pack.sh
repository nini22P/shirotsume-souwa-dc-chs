#!/bin/bash

set -euo pipefail

pip install --user numpy numba Pillow 

if [ -f "raw/data/SCR.MRG" ]; then
    echo "raw/data/ exists, skip GDI extraction"
elif [ -f "raw/gdi/disc.gdi" ]; then
    ./bin/buildgdi.exe -extract -gdi "raw/gdi/disc.gdi" -output "raw/data"
    echo "Extract GDI -> raw/data/"
else
    echo "Please put gdi files in raw/gdi/ and run again"
    exit 1
fi

if [ ! -d "build/data" ]; then
    echo "Copy raw/data -> build/data/"
    mkdir -p "build/data"
    find "raw/data" -maxdepth 1 -type f -iregex '.*\.\(acx\|afs\|bin\|drv\|hed\|mrg\|nam\|pvr\|sfd\)$' -exec cp -t "build/data/" {} +
fi

echo "Unpack scripts -> build/SCR/"
rm -rf "build/SCR"
mkdir -p "build/SCR"
python scr_tool.py unpack -i "raw/data/SCR.MRG" -o "build/SCR"

if [ ! -f "scr.csv" ]; then
    echo "Extract strings -> scr.csv"
    python script_tool.py extract -i "build/SCR" -o "scr.csv"
fi

echo "Mapping slots..."
python create_mapping.py -s scr.csv -b bin.csv -o build/mapping.csv

python patch_tool.py -b "raw/data/1ST_READ.BIN" -c "bin.csv" -e cp932 -m "build/mapping.csv" -o "build/data/1ST_READ.BIN"

python patch_encoding.py -b "build/data/1ST_READ.BIN" -c "build/mapping.csv"

echo "Import translations -> build/SCR/"
python script_tool.py import -i "build/SCR" -c "scr.csv" -m "build/mapping.csv"

echo "Repack scripts (encrypt) -> build/data/"
python scr_tool.py repack -i "build/SCR" -o "build/data/SCR.MRG"

echo "Rebuild SCRADR offsets"
python scradr_tool.py rebuild -i "raw/data/SCRADR.MRG" -o "build/data/SCRADR.MRG"

python create_layouts.py -m build/mapping.csv -o build/
echo "Layouts generated -> build/"

rebuild_syspvr_mrg() {
    local mrg_name=$1
    local default_font=$2
    local face_font=$3
    local raw_mrg="raw/data/${mrg_name}.MRG"
    local out_mrg="build/data/${mrg_name}.MRG"
    local work_dir="build/${mrg_name}"
    rm -rf "$work_dir"; mkdir -p "$work_dir"

    python syspvr_tool.py unpack -i "$raw_mrg" -o "$work_dir"
    echo "Unpack ${mrg_name}.MRG -> $work_dir/"

    python generate_texture.py -i build/18x22.txt --page-start 2 \
        --cell-w 18 --cell-h 22 --font "$default_font" --font-size 40 \
        --scale-x 0.95 --offset-y 8 -o "$work_dir"
    python generate_texture.py -i build/20x24.txt --page-start 17 \
        --cell-w 20 --cell-h 24 --font "$default_font" --font-size 46 \
        --scale-x 0.95 --offset-y 9 -o "$work_dir"

    python generate_texture.py -i build/18x22_face.txt --page-start 14 \
        --cell-w 18 --cell-h 22 --font "$face_font" --font-size 40 \
        --scale-x 0.95 --offset-y 8 -o "$work_dir"
    python generate_texture.py -i build/20x24_face.txt --page-start 33 \
        --cell-w 20 --cell-h 24 --font "$face_font" --font-size 46 \
        --scale-x 0.95 --offset-y 9 -o "$work_dir"

    python syspvr_tool.py pack -i "$work_dir" -o "$out_mrg"
}

rebuild_syspvr_mrg "SYSPVR"   "fonts/ChillDuanHeiSongBold.otf" "fonts/NotoSansCJKsc-Medium.otf"
rebuild_syspvr_mrg "SYSPVR_G" "fonts/NotoSansCJKsc-Medium.otf"  "fonts/NotoSansCJKsc-Medium.otf"

cp -f "build/data/SYSPVR.MRG" "build/data/SYSPVR_M.MRG"
cp -f "build/data/SYSPVR.MRG" "build/data/SYSPVR_P.MRG"
echo "SYSPVR_M.MRG, SYSPVR_P.MRG <- SYSPVR.MRG (copy)"

mkdir -p "build/gdi"
cp -f "raw/gdi/track01.bin" "raw/gdi/track02.raw" "build/gdi/"
./bin/buildgdi.exe -rebuild -gdi "raw/gdi/disc.gdi" -data "build/data/" -output "build/gdi/"
echo "Built GDI -> build/gdi/"

echo "Generating xdelta patch for track03.bin..."
./bin/xdelta3.exe -f -e -s "raw/gdi/track03.bin" "build/gdi/track03.bin" "build/gdi/track03.bin.xdelta"

echo "Done."

#!/usr/bin/env python3
"""
extract_pdf_comments.py (v2)

PDFに埋め込まれた注釈（ハイライト・下線・波線・取り消し線・付箋コメント）を、
該当ページ番号（本のノンブル）付きで抽出するスクリプト。

対応レイアウト:
  - 見開き2ページが1PDFページに収まった構成(左右にノンブルがある版)
  - 単ページ1PDFページ=1印刷ページの構成(ノンブルが中央/片側にある版)
  どちらもノンブルの検出数に応じて自動判定。

使い方:
  # 単一ファイル
  python3 extract_pdf_comments.py 入力.pdf
  python3 extract_pdf_comments.py 入力.pdf -o 出力.md
  python3 extract_pdf_comments.py 入力.pdf --format csv

  # フォルダ内のPDFを一括処理（PDFごとに個別ファイルを生成）
  python3 extract_pdf_comments.py --batch ./原稿フォルダ

依存パッケージ:
  pip install pymupdf --break-system-packages
"""

import argparse
import csv
import glob
import os
import sys
from pathlib import Path

import fitz  # PyMuPDF

# ハイライト系の注釈タイプ（下線・波線・取り消し線も対象に含める）
MARKUP_ANNOT_TYPES = (8, 9, 10, 11)  # Highlight, Underline, Squiggly, StrikeOut


def get_highlighted_text(annot, page):
    """
    ハイライト等の下にある本文テキストを、頂点座標(Quad)ベースで抽出する。
    外接矩形(bounding rect)だけを使う方式より、複数行にまたがるハイライトや
    表組み・段組みをまたぐケースでの精度が高い。
    """
    if annot.type[0] not in MARKUP_ANNOT_TYPES:
        return ""

    text_content = []
    try:
        quads = annot.vertices
        if not quads:
            return ""
        for i in range(0, len(quads), 4):
            quad = fitz.Quad(quads[i], quads[i + 1], quads[i + 2], quads[i + 3])
            text = page.get_text("text", clip=quad.rect).strip()
            if text:
                text_content.append(text)
        return "".join(text_content).replace("\r", "").replace("\n", "")
    except Exception:
        return ""


def normalize_newlines(text):
    """コメント内の改行コードを標準的な '\\n' に統一する。"""
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def get_footer_page_numbers(page, footer_ratio: float):
    """ページ下部（footer_ratio以降の高さ）にある数字だけの単語を
    x座標順（左→右）に取得する。見開きなら2つ、単ページなら1つが典型。
    """
    words = page.get_text("words")
    h = page.rect.height
    footer_words = [
        w for w in words
        if w[3] > h * footer_ratio and w[4].strip().isdigit()
    ]
    footer_words.sort(key=lambda w: w[0])
    return [w[4] for w in footer_words]


def resolve_page_number(page, annot_rect, nums):
    """注釈の位置とノンブル候補から、対応する印刷ページ番号を推定する。"""
    if not nums:
        return None
    if len(nums) == 1:
        return nums[0]
    mid_x = page.rect.width / 2
    center = (annot_rect.x0 + annot_rect.x1) / 2
    return nums[0] if center < mid_x else nums[-1]


def extract_comments(pdf_path: str, footer_ratio: float = 0.9):
    """1つのPDFから注釈を抽出し、辞書のリストを返す。"""
    doc = fitz.open(pdf_path)
    results = []

    for pdf_page_index, page in enumerate(doc):
        annots = list(page.annots())
        if not annots:
            continue

        nums = get_footer_page_numbers(page, footer_ratio)

        for annot in annots:
            info = annot.info
            content = normalize_newlines(info.get("content", ""))
            quoted_text = get_highlighted_text(annot, page)

            if not content and not quoted_text:
                continue  # コメントも対象文もない注釈（色付けのみ等）は除外

            page_no = resolve_page_number(page, annot.rect, nums)

            results.append({
                "book_page": page_no if page_no is not None else "",
                "pdf_page": pdf_page_index + 1,
                "annot_type": annot.type[1],
                "quoted": quoted_text,
                "comment": content,
                "modified": info.get("modDate", ""),
            })

    doc.close()

    def sort_key(r):
        try:
            return (0, int(r["book_page"]))
        except (ValueError, TypeError):
            return (1, r["pdf_page"])

    results.sort(key=sort_key)
    return results


def write_markdown(results, out_path: str, title: str = "著者コメント一覧"):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(f"合計 {len(results)} 件\n\n")
        current_page = None
        for r in results:
            page_label = r["book_page"] or f"(PDFページ{r['pdf_page']})"
            if page_label != current_page:
                current_page = page_label
                f.write(f"\n## p.{page_label}\n\n")
            if r["quoted"]:
                f.write(f"- **該当箇所**: 「{r['quoted']}」\n")
                f.write(f"  **コメント**: {r['comment']}\n\n")
            else:
                f.write(f"- **コメント**: {r['comment']}(場所指定なし)\n\n")


def write_csv(results, out_path: str):
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["book_page", "pdf_page", "annot_type", "quoted", "comment", "modified"],
        )
        writer.writeheader()
        for r in results:
            writer.writerow(r)


def process_single(pdf_path: Path, out_path: Path, fmt: str, footer_ratio: float):
    results = extract_comments(str(pdf_path), footer_ratio=footer_ratio)
    if fmt == "csv":
        write_csv(results, str(out_path))
    else:
        write_markdown(results, str(out_path), title=pdf_path.stem)
    print(f"作成完了: {out_path.name} ({len(results)}件)")
    return len(results)


def process_batch(input_dir: str, fmt: str, footer_ratio: float):
    input_dir = os.path.abspath(input_dir)
    pdf_files = sorted(glob.glob(os.path.join(input_dir, "*.pdf")))

    if not pdf_files:
        print(f"エラー: 指定されたフォルダ '{input_dir}' にPDFファイルが見つかりません。", file=sys.stderr)
        return

    print(f"対象フォルダ: {input_dir}")
    print(f"{len(pdf_files)} 個のPDFファイルを処理します...\n")

    success_count = 0
    for pdf_path_str in pdf_files:
        pdf_path = Path(pdf_path_str)
        out_path = pdf_path.with_suffix(f".{fmt}")
        try:
            process_single(pdf_path, out_path, fmt, footer_ratio)
            success_count += 1
        except Exception as e:
            print(f"エラー ({pdf_path.name}): {e}", file=sys.stderr)

    print("-" * 30)
    print(f"処理完了: {success_count}/{len(pdf_files)} ファイル")


def main():
    parser = argparse.ArgumentParser(description="PDF注釈をページ番号付きで抽出する")
    parser.add_argument("pdf_path", nargs="?", help="入力PDFファイルのパス（単一ファイルモード）")
    parser.add_argument("-o", "--output", help="出力ファイルパス(省略時は入力ファイル名+拡張子)")
    parser.add_argument("--format", choices=["md", "csv"], default="md", help="出力形式(既定: md)")
    parser.add_argument(
        "--footer-ratio", type=float, default=0.9,
        help="ページ下部何割からをノンブル検出対象にするか(既定: 0.9 = 下部10%)",
    )
    parser.add_argument(
        "--batch", metavar="DIR",
        help="指定フォルダ内のPDFを一括処理し、PDFごとに個別ファイルを生成する",
    )
    args = parser.parse_args()

    if args.batch:
        process_batch(args.batch, args.format, args.footer_ratio)
        return

    if not args.pdf_path:
        parser.error("pdf_path を指定するか、--batch DIR を指定してください。")

    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        print(f"エラー: ファイルが見つかりません: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    if pdf_path.is_dir():
        # フォルダが渡された場合は自動でバッチモードとして処理する
        print(f"（フォルダが指定されたため、--batch モードとして処理します）")
        process_batch(str(pdf_path), args.format, args.footer_ratio)
        return

    out_path = Path(args.output) if args.output else pdf_path.with_suffix(f".{args.format}")
    process_single(pdf_path, out_path, args.format, args.footer_ratio)


if __name__ == "__main__":
    main()
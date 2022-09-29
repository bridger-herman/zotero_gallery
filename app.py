import json
import os
import sqlite3
from pathlib import Path
from extract_html_images import extract_html_images

from extract_pdf_images import extract_pdf_images

ZOTERO_DATA_DIR = Path('~/Zotero').expanduser().resolve()
ZOTERO_DB = ZOTERO_DATA_DIR.joinpath('zotero.sqlite')
BBT_DB = ZOTERO_DATA_DIR.joinpath('better-bibtex.sqlite')
STORAGE = 'storage/'
STORAGE_DB = 'storage:'
STORAGE_DIR = ZOTERO_DATA_DIR.joinpath(STORAGE)
OUTPUT_FOLDER = Path('images')
ZOTERO_GALLERY_COLLECTION_NAME = '_Gallery'

EXTRACTORS = {
    'application/pdf': extract_pdf_images,
    'text/html': extract_html_images,
}

if not OUTPUT_FOLDER.exists():
    os.makedirs(OUTPUT_FOLDER)

# better bibtex cursor
# better bibtex just shoves stuff in JSON...
con_bbt = sqlite3.connect('file:' + str(BBT_DB) + '?mode=ro', uri=True)
cur_bbt = con_bbt.cursor()
better_bibtex = cur_bbt.execute('SELECT * FROM "better-bibtex" WHERE name = "better-bibtex.citekey"')
name, json_bibtex = better_bibtex.fetchone()
bibtex = json.loads(json_bibtex)['data']


# main zotero cursor
con = sqlite3.connect('file:' + str(ZOTERO_DB) + '?mode=ro', uri=True)
cur = con.cursor()


def extract_images():
    # find gallery collection in zotero and get all publications in it
    gallery_id = cur.execute(f'SELECT collectionID FROM collections WHERE collectionName = "{ZOTERO_GALLERY_COLLECTION_NAME}"').fetchone()[0]
    # get all publications in gallery collection
    gallery_pub_ids = tuple(map(lambda i: i[0], cur.execute(f'SELECT itemID FROM collectionItems WHERE collectionID = "{gallery_id}"').fetchall()))
    gallery_pubs = cur.execute(f'SELECT itemID, key FROM items WHERE itemID IN {gallery_pub_ids}').fetchall()

    for item_id, item_key in gallery_pubs:
        bbt_key = next(filter(lambda e: e['itemKey'] == item_key, bibtex))['citekey']
        print(bbt_key)
        attachments = cur.execute(f'SELECT itemID, contentType, path FROM itemAttachments WHERE parentItemID = {item_id}')
        for attachment_id, content_type, attachment_file in attachments.fetchall():
            # lookup canonical attachment ID in main `items` table
            attachment_key = cur.execute(f'SELECT key FROM items WHERE itemID = {attachment_id}').fetchone()[0]
            # input path
            attachment_path = STORAGE_DIR.joinpath(attachment_key).joinpath(str(attachment_file).replace(STORAGE_DB, ''))
            # output path
            image_path = OUTPUT_FOLDER.joinpath(bbt_key)
            new_pub = False
            if not image_path.exists():
                new_pub = True

            # extract images from each publication based on its type
            print(' ' * 3, content_type, attachment_path)
            if new_pub and input('        New publication, extract images? (Y/n): ').lower() != 'n':
                print(' ' * 7, 'Extracting Images...', end='')

                os.makedirs(image_path)
                try:
                    EXTRACTORS[content_type](image_path, attachment_path)
                except KeyError:
                    print('Extractor not found for type', content_type)

                print('done')

if __name__ == '__main__':
    extract_images()
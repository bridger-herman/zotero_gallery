import json
import os
import sqlite3
from pathlib import Path
from flask import Flask, render_template, g, request

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

FLASK_HOST = '127.0.0.1'
FLASK_PORT = 5000
FLASK_DEBUG = True

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

# gallery sqlite
con_gallery = sqlite3.connect('gallery.sqlite')
cur_gallery = con_gallery.cursor()

try:
    cur_gallery.execute('CREATE TABLE gallery (itemKey TEXT PRIMARY KEY NOT NULL, imageIndex INT DEFAULT 0);')
except sqlite3.OperationalError:
    pass

# Flask web app
app = Flask(__name__, static_folder='images')


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

                # Create db entry and folder to store images
                cur_gallery.execute(f'INSERT INTO gallery (itemKey) VALUES ("{bbt_key}");')
                con_gallery.commit()
                os.makedirs(image_path)
                try:
                    EXTRACTORS[content_type](image_path, attachment_path)
                except KeyError:
                    print('Extractor not found for type', content_type)

                print('done')
    print('Finished extracting images.')
    con_gallery.close()

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect('gallery.sqlite')
    return db

@app.teardown_appcontext
def close_connection(exception):
    if exception is not None:
        print(exception)
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# At this point, source of truth is now the 'images' folder.
def get_pub_images():
    pub_keys = os.listdir(OUTPUT_FOLDER)
    publication_images = {}
    for pub_key in pub_keys:
        pub_folder = OUTPUT_FOLDER.joinpath(pub_key)
        pub_list = []
        for img in os.listdir(pub_folder):
            pub_list.append(pub_folder.joinpath(img))
        publication_images[pub_key] = pub_list
    return publication_images

def get_img_preview_indices():
    pub_keys = get_db().cursor().execute('SELECT itemKey, imageIndex FROM gallery')
    indices = {}
    for key, index in pub_keys.fetchall():
        indices[key] = index
    return indices

@app.route('/api/incrementImageIndex/<string:itemKey>', methods=['POST'])
def increment_img_index(itemKey):
    inc = request.json['increase']
    value = 1 if inc else -1
    current_value = get_img_preview_indices()[itemKey]
    max_value = len(get_pub_images()[itemKey])
    new_index = max(0, min(current_value + value, max_value))
    db = get_db()
    db.cursor().execute(f'UPDATE gallery SET imageIndex = {new_index} WHERE itemKey = "{itemKey}"')
    db.commit()

    out = f'Index for {itemKey} is now {new_index}'
    print(out)
    return out

@app.route('/')
def index():
    publications = get_pub_images()
    preview_indices = get_img_preview_indices()
    return render_template('index.html', publications=publications, preview_indices=preview_indices)

if __name__ == '__main__':
    extract_images()
    app.run(FLASK_HOST, FLASK_PORT, debug=FLASK_DEBUG)

    con.close()
    con_bbt.close()
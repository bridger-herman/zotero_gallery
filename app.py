import json
import os
import sqlite3
from pathlib import Path
from flask import Flask, render_template, g, request
from livereload import Server

from extract_html_images import extract_html_images
from extract_pdf_images import extract_pdf_images

ZOTERO_DATA_DIR = Path('~/Zotero').expanduser().resolve()
ZOTERO_DB = ZOTERO_DATA_DIR.joinpath('zotero.sqlite')
BBT_DB = ZOTERO_DATA_DIR.joinpath('better-bibtex.sqlite')
GALLERY_DB = Path('./gallery.sqlite')
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

# Flask web app
app = Flask(__name__, static_folder='images')

def extract_images():
    # Pretend to be a Flask app
    with app.app_context():
        # Set up Better BibTeX
        # better bibtex just shoves stuff in JSON...
        con_bbt = get_bbt_db()
        cur_bbt = con_bbt.cursor()
        better_bibtex = cur_bbt.execute('SELECT * FROM "better-bibtex" WHERE name = "better-bibtex.citekey"')
        name, json_bibtex = better_bibtex.fetchone()
        bibtex = json.loads(json_bibtex)['data']

        # main zotero cursor
        con_zotero = get_zotero_db()
        cur_zotero = con_zotero.cursor()

        # Connect to gallery and set up `gallery` table if not done already
        con_gallery = get_gallery_db()
        cur_gallery = con_gallery.cursor()
        try:
            cur_gallery.execute('CREATE TABLE gallery (itemKey TEXT PRIMARY KEY NOT NULL, imageIndex INT DEFAULT 0);')
        except sqlite3.OperationalError:
            pass

        # find gallery collection in zotero and get all publications in it
        gallery_id = cur_zotero.execute(f'SELECT collectionID FROM collections WHERE collectionName = "{ZOTERO_GALLERY_COLLECTION_NAME}"').fetchone()[0]
        # get all publications in gallery collection
        gallery_pub_ids = tuple(map(lambda i: i[0], cur_zotero.execute(f'SELECT itemID FROM collectionItems WHERE collectionID = "{gallery_id}"').fetchall()))
        gallery_pubs = cur_zotero.execute(f'SELECT itemID, key FROM items WHERE itemID IN {gallery_pub_ids}').fetchall()

        for i, (item_id, item_key) in enumerate(gallery_pubs):
            bbt_key = next(filter(lambda e: e['itemKey'] == item_key, bibtex))['citekey']
            print('Extracting images for', bbt_key, '({:.0%} done)'.format((i + 1) / len(gallery_pubs)))
            attachments = cur_zotero.execute(f'SELECT itemID, contentType, path FROM itemAttachments WHERE parentItemID = {item_id}')
            for attachment_id, content_type, attachment_file in attachments.fetchall():
                # lookup canonical attachment ID in main `items` table
                attachment_key = cur_zotero.execute(f'SELECT key FROM items WHERE itemID = {attachment_id}').fetchone()[0]
                # input path
                attachment_path = STORAGE_DIR.joinpath(attachment_key).joinpath(str(attachment_file).replace(STORAGE_DB, ''))
                # output path
                image_path = OUTPUT_FOLDER.joinpath(bbt_key)
                new_pub = False
                if not image_path.exists():
                    new_pub = True

                # extract images from each publication based on its type
                if new_pub:
                    # Create db entry and folder to store images
                    try:
                        cur_gallery.execute(f'INSERT INTO gallery (itemKey) VALUES ("{bbt_key}");')
                        con_gallery.commit()
                    except sqlite3.IntegrityError:
                        print(bbt_key, 'already exists in database')
                    os.makedirs(image_path)
                    try:
                        EXTRACTORS[content_type](image_path, attachment_path)
                    except KeyError:
                        print('Extractor not found for type', content_type)

        print('Finished extracting images.')
        con_gallery.close()

# Database functions (internal gallery, zotero, and better bibtex)
# Gallery database for storing the gallery items
def get_gallery_db():
    db = getattr(g, 'gallery_db', None)
    if db is None:
        g.gallery_db = sqlite3.connect(GALLERY_DB)
    return g.gallery_db

# Main Zotero database
def get_zotero_db():
    db = getattr(g, 'zotero_db', None)
    if db is None:
        g.zotero_db = sqlite3.connect('file:' + str(ZOTERO_DB) + '?mode=ro', uri=True)
    return g.zotero_db

# Better BibTeX database
def get_bbt_db():
    db = getattr(g, 'bbt_db', None)
    if db is None:
        g.bbt_db = sqlite3.connect('file:' + str(BBT_DB) + '?mode=ro', uri=True)
    return g.bbt_db

@app.teardown_appcontext
def close_connection(exception):
    if exception is not None:
        print(exception)
    for db_name in ['gallery_db', 'zotero_db', 'bbt_db']:
        db = getattr(g, db_name, None)
        if db is not None:
            db.close()

# Flask Helpers
# At this point, source of truth is now the 'images' folder.
def get_pub_images():
    pub_keys = os.listdir(OUTPUT_FOLDER)
    publication_images = {}
    for pub_key in pub_keys:
        pub_folder = OUTPUT_FOLDER.joinpath(pub_key)
        pub_list = []
        for img in os.listdir(pub_folder):
            pub_list.append(pub_folder.joinpath(img).as_posix())
        publication_images[pub_key] = pub_list
    return publication_images

def get_img_preview_indices():
    pub_keys = get_gallery_db().cursor().execute('SELECT itemKey, imageIndex FROM gallery')
    indices = {}
    for key, index in pub_keys.fetchall():
        indices[key] = index
    return indices

# Flask Routes
@app.route('/api/incrementImageIndex/<string:itemKey>', methods=['POST'])
def increment_img_index(itemKey):
    inc = request.json['increase']
    value = 1 if inc else -1
    current_value = get_img_preview_indices()[itemKey]
    max_value = len(get_pub_images()[itemKey])
    new_index = max(0, min(current_value + value, max_value))
    db = get_gallery_db()
    db.cursor().execute(f'UPDATE gallery SET imageIndex = {new_index} WHERE itemKey = "{itemKey}"')
    db.commit()

    out = f'Index for {itemKey} is now {new_index}'
    print(out)
    return out

@app.route('/api/getPublications')
def get_publications():
    return get_pub_images()

@app.route('/')
def index():
    publications = get_pub_images()
    preview_indices = get_img_preview_indices()
    return render_template('index.html', publications=publications, preview_indices=preview_indices)

if __name__ == '__main__':
    extract_images()
    app.debug = FLASK_DEBUG

    server = Server(app.wsgi_app)
    server.application(FLASK_PORT, FLASK_HOST)
    server.serve()
import sys
import shutil
import json
import os
import sqlite3
from pathlib import Path
from flask import Flask, render_template, g, request, send_from_directory
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
            cur_gallery.execute('CREATE TABLE gallery (itemKey TEXT PRIMARY KEY NOT NULL, previewImageIndex INT DEFAULT 0, zoteroItemID INT);')
        except sqlite3.OperationalError:
            pass

        # find gallery collection in zotero and get all publications in it
        gallery_id = cur_zotero.execute(f'SELECT collectionID FROM collections WHERE collectionName = "{ZOTERO_GALLERY_COLLECTION_NAME}"').fetchone()[0]
        # get all publications in gallery collection
        gallery_pub_ids = tuple(map(lambda i: i[0], cur_zotero.execute(f'SELECT itemID FROM collectionItems WHERE collectionID = "{gallery_id}"').fetchall()))
        gallery_pubs = cur_zotero.execute(f'SELECT itemID, key FROM items WHERE itemID IN {gallery_pub_ids}').fetchall()

        new_pubs = 0
        for i, (item_id, item_key) in enumerate(gallery_pubs):
            bbt_key = next(filter(lambda e: e['itemKey'] == item_key, bibtex))['citekey']
            attachments = cur_zotero.execute(f'SELECT itemID, contentType, path FROM itemAttachments WHERE parentItemID = {item_id}')
            attachments_list = sorted(attachments.fetchall(), key=lambda c: c[1])

            # output path
            image_path = OUTPUT_FOLDER.joinpath(bbt_key)
            # check if publication output folder exists
            new_pub = False
            if not image_path.exists():
                print('Found new publication', bbt_key, 'extracting images ({:.0%} done)'.format((i + 1) / len(gallery_pubs)))
                new_pub = True
                new_pubs += 1

            for attachment_id, content_type, attachment_file in attachments_list:
                # lookup canonical attachment ID in main `items` table
                attachment_key = cur_zotero.execute(f'SELECT key FROM items WHERE itemID = {attachment_id}').fetchone()[0]
                # input path
                attachment_path = get_attachment_path(attachment_key, attachment_file)
                # Create db entry
                try:
                    cur_gallery.execute(f'INSERT INTO gallery (itemKey, zoteroItemID) VALUES ("{bbt_key}", {item_id});')
                    con_gallery.commit()
                    print('Inserted key', bbt_key, 'into gallery database, zotero id', item_id)
                except sqlite3.IntegrityError:
                    pass

                # extract images from each publication based on its type
                if new_pub:
                    # create folder to store images
                    os.makedirs(image_path)
                    try:
                        EXTRACTORS[content_type](image_path, attachment_path)
                    except KeyError:
                        print('Extractor not found for type', content_type)

        print('Finished extracting images ({} new publications found)'.format(new_pubs))
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

# Construct the path of a Zotero attachment
def get_attachment_path(attachment_key, attachment_file):
    return STORAGE_DIR.joinpath(attachment_key).joinpath(str(attachment_file).replace(STORAGE_DB, ''))

# Get key:value pairs of tagID:tag
def get_tags():
    cur_zotero = get_zotero_db().cursor()

    # Get tag list for helpfulness later
    tags_res = cur_zotero.execute('SELECT * FROM tags')
    return dict(tags_res.fetchall())

# Flask Helpers
# Get all publications so we can display them on the page
# - publication citation key (better bibtex)
#   - zoteroItemID: int -- associated publication in the zotero database for this publication
#   - images: list<str> -- list of all images associated with this publication. if images have been 'minified' already, this will only have one item.
#   - previewImage: int -- index out of `images` to display for this publication's 'preview' on the gallery page
#   - tags: list<str> -- list of zotero tags associated with this publication
#   - fileLink: <str> -- link to the local zotero file attachment where this pub can be found
#   - info:
#       - title: str -- full title of publication
#       - authors: list<str> -- all authors in publication
#       - date: <str> -- date of publication (usually just year...)
def get_publications():
    # Set up databases
    cur_gallery = get_gallery_db().cursor()
    cur_zotero = get_zotero_db().cursor()

    # Get tag list and field list for decoding tagIDs/fieldIDs later
    tags = get_tags()
    fields_res = cur_zotero.execute('SELECT fieldID, fieldName FROM fields')
    fields = dict(fields_res.fetchall())

    pub_keys = os.listdir(OUTPUT_FOLDER)
    publications = {}
    for pub_key in pub_keys:
        pub_data = {}
        # Query gallery db for information (`zoteroItemID`, `previewImageIndex`)
        gallery_result = cur_gallery.execute(f'SELECT zoteroItemID, previewImageIndex FROM gallery WHERE itemKey = "{pub_key}"')
        zotero_id, preview_index = gallery_result.fetchone()
        pub_data['zoteroItemID'] = zotero_id
        pub_data['previewImageIndex'] = preview_index

        # Get `images` list
        pub_folder = OUTPUT_FOLDER.joinpath(pub_key)
        img_list = []
        for img in os.listdir(pub_folder):
            img_list.append(pub_folder.joinpath(img).as_posix())
        pub_data['images'] = img_list

        # Look into zotero db for tag
        tag_ids_res = cur_zotero.execute(f'SELECT tagID FROM itemTags WHERE itemID = {zotero_id}')
        pub_tags = [tags[tid] for (tid,) in tag_ids_res.fetchall()]
        pub_data['tags'] = pub_tags

        # Look into zotero db for title, author, date, etc. info
        fields_values = cur_zotero.execute(f'SELECT valueID, fieldID FROM itemData WHERE itemID = {zotero_id}')
        value_id_to_field_id = dict(fields_values.fetchall())
        values = cur_zotero.execute(f'''
            SELECT itemDataValues.valueID, value FROM itemDataValues
                INNER JOIN itemData ON itemDataValues.valueID = itemData.valueID AND itemData.itemID = {zotero_id}
        ''')
        value_id_to_value = dict(values.fetchall())

        # field_name: field_value
        pub_info = {fields[value_id_to_field_id[value_id]]: value for value_id, value in value_id_to_value.items()}
        pub_data['info'] = pub_info

        # Gather attachment file path to open as file:/// URI in-browser
        try:
            attachment_res = cur_zotero.execute(f'SELECT path, itemID FROM itemAttachments WHERE parentItemID = {zotero_id}')
            first_attach_path, attach_id = attachment_res.fetchone()
            zotero_key_res = cur_zotero.execute(f'SELECT key FROM items WHERE itemID = {attach_id}')
            (zotero_key, ) = zotero_key_res.fetchone()
            pub_data['fileLink'] = zotero_key + '/' + first_attach_path.replace(STORAGE_DB, '')
        except:
            # Skip if no attachments
            pass

        publications[pub_key] = pub_data
    return publications

def get_img_preview_indices():
    pub_keys = get_gallery_db().cursor().execute('SELECT itemKey, previewImageIndex FROM gallery')
    indices = {}
    for key, index in pub_keys.fetchall():
        indices[key] = index
    return indices

# Flask Routes
@app.route('/api/incrementImageIndex/<string:itemKey>/<int:increase>', methods=['POST'])
def increment_img_index(itemKey, increase):
    value = 1 if increase > 0 else -1
    current_value = get_img_preview_indices()[itemKey]
    max_value = len(get_publications()[itemKey]['images'])
    new_index = max(0, min(current_value + value, max_value))
    db = get_gallery_db()
    db.cursor().execute(f'UPDATE gallery SET previewImageIndex = {new_index} WHERE itemKey = "{itemKey}"')
    db.commit()

    out = f'Index for {itemKey} is now {new_index}'
    print(out)
    return out

@app.route('/api/getPublications')
def api_get_publications():
    return get_publications()

@app.route('/api/getAttachment/<path:filename>')
def get_zotero_attachment(filename):
    return send_from_directory(STORAGE_DIR, filename)

@app.route('/')
def index():
    publications = get_publications()
    preview_indices = get_img_preview_indices()
    return render_template('index.html', publications=publications, preview_indices=preview_indices)

def remove_entry(entry_key):
    out_folder = OUTPUT_FOLDER.joinpath(entry_key)
    if os.path.exists(out_folder):
        shutil.rmtree(out_folder)
        print('removed folder', out_folder)

    with app.app_context():
        con_gallery = get_gallery_db()
        cur_gallery = con_gallery.cursor()
        cur_gallery.execute(f'DELETE FROM gallery WHERE itemKey = "{entry_key}"')
        con_gallery.commit()
        print('removed entry', entry_key, 'from gallery database')


def print_help():
    app_help = '''
usage: python3 ./app.py <options>

options:
run <debug>: run the gallery server (optionally in debug mode)
extract:    extract images from any new publications in the Zotero database
pack:       reduce every publication to a single selected image and create a zip file in the `data` folder containing all images.
pull:       pull databases from Zotero and make a backup in case something goes wrong.
push:       push databases to Zotero and make a backup in case something goes wrong.
remove <entry_key>: remove the bibtex entry key from the database and images gallery
'''
    print(app_help)

if __name__ == '__main__':
    # remove_entry('laidlawComparing2DVector2005')
    # remove_entry('forsbergComparing3DVector2009')
    # exit(0)

    if len(sys.argv) == 1:
        print_help()
        exit(1)

    elif 'extract' in sys.argv:
        extract_images()
        exit(0)

    elif 'remove' in sys.argv:
        if len(sys.argv) == 3:
            remove_entry(sys.argv[2])
            exit(0)
        else:
            print_help()
            exit(1)

    elif 'run' in sys.argv:
        debug = 'debug' in sys.argv
        app.debug = debug

        if debug:
            server = Server(app.wsgi_app)
            server.application(FLASK_PORT, FLASK_HOST)
            server.serve()
        else:
            app.run(FLASK_HOST, FLASK_PORT)
    else:
        print(f'command `${sys.argv[1]}` not implemented')
import sys
import shutil
import json
import os
import sqlite3
from pathlib import Path
from flask import Flask, render_template, g, request, send_from_directory
from livereload import Server
import zipfile

from extract_html_images import extract_html_images
from extract_pdf_images import extract_pdf_images

GALLERY_DATA_DIR = Path('./data')
if not GALLERY_DATA_DIR.exists():
    os.makedirs(GALLERY_DATA_DIR)
GALLERY_DATA_DIR = GALLERY_DATA_DIR.resolve()

ZOTERO_DATA_DIR = Path('~/Zotero').expanduser().resolve()

ZOTERO_DB_NAME = 'zotero.sqlite'
BBT_DB_NAME = 'better-bibtex.sqlite'
ZOTERO_SRC_DB = ZOTERO_DATA_DIR.joinpath(ZOTERO_DB_NAME)
BBT_SRC_DB = ZOTERO_DATA_DIR.joinpath(BBT_DB_NAME)
ZOTERO_GALLERY_DB = GALLERY_DATA_DIR.joinpath(ZOTERO_DB_NAME)
BBT_GALLERY_DB = GALLERY_DATA_DIR.joinpath(BBT_DB_NAME)

GALLERY_DB = GALLERY_DATA_DIR.joinpath('gallery.sqlite')
STORAGE = 'storage/'
STORAGE_DB = 'storage:'
STORAGE_DIR = ZOTERO_DATA_DIR.joinpath(STORAGE)
PUBS_FOLDER = Path('images')
ZOTERO_GALLERY_COLLECTION_NAME = '_GalleryTest'
PREVIEW_INDEX_PACKED = -1

GALLERY_ZIP = GALLERY_DATA_DIR.joinpath('gallery.zip')
SYNC_PUB_TAG = 'z_Gallery_Sync_Placeholder'

EXTRACTORS = {
    'application/pdf': extract_pdf_images,
    'text/html': extract_html_images,
}

FLASK_HOST = '127.0.0.1'
FLASK_PORT = 5000
FLASK_DEBUG = True

if not PUBS_FOLDER.exists():
    os.makedirs(PUBS_FOLDER)

# Flask web app
app = Flask(__name__, static_folder='images')

def pull(include_gallery_data=True):
    '''
    Pull (make a copy of) the Zotero databases so we can run the gallery at the
    same time as Zotero.

    Additionally, unpack the gallery.zip file into the images dir.

    Make a backup in case something goes wrong.
    '''
    print('Pulling...')
    # make backups
    zotero_bak = Path(str(ZOTERO_GALLERY_DB) + '.bak')
    bbt_bak = Path(str(BBT_GALLERY_DB) + '.bak')
    gallery_db_bak = Path(str(GALLERY_DB) + '.bak')
    gallery_zip_bak = Path(str(GALLERY_ZIP) + '.bak')
    if ZOTERO_GALLERY_DB.exists():
        shutil.copyfile(ZOTERO_GALLERY_DB, zotero_bak)
    if BBT_GALLERY_DB.exists():
        shutil.copyfile(BBT_GALLERY_DB, bbt_bak)
    if GALLERY_DB.exists():
        shutil.copyfile(GALLERY_DB, gallery_db_bak)
    if GALLERY_ZIP.exists() and include_gallery_data:
        shutil.copyfile(GALLERY_ZIP, gallery_zip_bak)
    print('    - made backups')

    # then, make a copy of the Zotero databases in local data folder
    shutil.copyfile(ZOTERO_SRC_DB, ZOTERO_GALLERY_DB)
    shutil.copyfile(BBT_SRC_DB, BBT_GALLERY_DB)
    print('    - copied zotero database and better bibtex database')

    # copy gallery database and gallery images
    if include_gallery_data:
        sync_paths = get_gallery_sync_attachment_paths()
        if sync_paths is not None:
            shutil.copyfile(sync_paths[GALLERY_DB.name], GALLERY_DB)
            shutil.copyfile(sync_paths[GALLERY_ZIP.name], GALLERY_ZIP)
            print('    - copied gallery database and archive')

            # extract/unpack gallery image archive
            unpack()
            print('    - extracted gallery archive')
        else:
            print('    - failed to copy gallery database and archive')
            print('    - failed to extract gallery archive')


def push():
    '''
    Push (copy gallery.sqlite and gallery.zip to) zotero publication entry that
    has an attachment storing these databases for easy syncing across devices.

    Additionally, pack the publication gallery images and zip them up into gallery.zip.

    Make a backup in case something goes wrong.
    '''
    # make backups
    zotero_bak = Path(str(ZOTERO_SRC_DB) + '.gallery.bak')
    bbt_bak = Path(str(BBT_SRC_DB) + '.gallery.bak')
    if ZOTERO_SRC_DB.exists():
        shutil.copyfile(ZOTERO_SRC_DB, zotero_bak)
    if BBT_SRC_DB.exists():
        shutil.copyfile(BBT_SRC_DB, bbt_bak)

    pack()
    print('    - packed gallery images into archive')

    sync_paths = get_gallery_sync_attachment_paths()
    if sync_paths is not None:
        shutil.copyfile(GALLERY_DB, sync_paths[GALLERY_DB.name])
        shutil.copyfile(GALLERY_ZIP, sync_paths[GALLERY_ZIP.name])
        print('    - copied gallery database and archive')
    else:
        print('    - failed copy database and archive')

def get_gallery_sync_attachment_paths():
    # pack stuff up
    with app.app_context():
        cur_zotero = get_zotero_db().cursor()

        # Get the publication gallery stuff is stored in
        all_tags = get_tags()
        try:
            tag_id, _sync_tag = next(filter(lambda p: p[1] == SYNC_PUB_TAG, all_tags.items()))
        except StopIteration:
            print('Gallery storage tag `{}` not found. Please tag a placeholder entry in Zotero.'.format(SYNC_PUB_TAG))
            return None
        pub_id_res = cur_zotero.execute(f'SELECT itemID FROM itemTags WHERE tagID = {tag_id}')
        (item_id, ) = pub_id_res.fetchone()

        # Get attachments and verify they're all present
        expected_attachment_names = [GALLERY_DB.name, GALLERY_ZIP.name]
        attachs_res = cur_zotero.execute(f'SELECT itemID, contentType, path FROM itemAttachments WHERE parentItemID = {item_id}')
        actual_attachments = {n: None for n in expected_attachment_names}
        for attach_id, content_type, path in attachs_res.fetchall():
            actual_filename = path.replace(STORAGE_DB, '')
            if actual_filename in expected_attachment_names:
                # lookup canonical attachment ID in main `items` table
                attachment_key = cur_zotero.execute(f'SELECT key FROM items WHERE itemID = {attach_id}').fetchone()[0]
                actual_attachments[actual_filename] = get_attachment_path(attachment_key, actual_filename)
            else:
                print('Warning: unexpected attachment ', actual_filename)
        return actual_attachments

def pack():
    '''
    Reduce the number of extracted images in each publication directory to a
    single one and update the zotero gallery database accordingly (specify -1
    for every publication index to indicate that there's only ONE image and it's
    no longer adjustable.)

    Additionally, zip up all publication images into gallery.zip.

    Make a backup database and gallery.zip before proceeding.
    '''
    print('Packing publication images...')
    # make backups
    gallery_db_bak = Path(str(GALLERY_DB) + '.bak')
    gallery_zip_bak = Path(str(GALLERY_ZIP) + '.bak')
    if GALLERY_DB.exists():
        shutil.copyfile(GALLERY_DB, gallery_db_bak)
    if GALLERY_ZIP.exists():
        shutil.copyfile(GALLERY_ZIP, gallery_zip_bak)
    print('    - made backups')

    with app.app_context():
        con_gallery = get_gallery_db()
        cur_gallery = con_gallery.cursor()

        # get rid of superfluous publication images
        imgs_removed = 0
        for pub_key in os.listdir(PUBS_FOLDER):
            pub_path = PUBS_FOLDER.joinpath(pub_key)

            # find actual image index and get nth image
            res = cur_gallery.execute(f'SELECT previewImageIndex FROM gallery WHERE itemKey = "{pub_key}"')
            try:
                (img_index, ) = res.fetchone()
            except TypeError:
                print('Publication not found: ', pub_key, ', skipping')
                continue

            # if <0 already, already packed...
            if img_index < 0:
                continue

            all_imgs = list(sorted(os.listdir(pub_path)))
            for i, img in enumerate(all_imgs):
                if i != img_index:
                    img_path = pub_path.joinpath(img)
                    os.unlink(img_path)
                    cur_gallery.execute(f'UPDATE gallery SET previewImageIndex = {PREVIEW_INDEX_PACKED} WHERE itemKey = "{pub_key}"')
                    imgs_removed += 1

        con_gallery.commit()
    print(f'    - removed {imgs_removed} images')

    # rewrite zip file (copy all folders/single images in)
    z = zipfile.ZipFile(GALLERY_ZIP, 'w')
    print('    - generating zip file')
    all_pubs = os.listdir(PUBS_FOLDER)
    for i, pub_key in enumerate(all_pubs):
        if i % max(1, len(all_pubs) // 10) == 0:
            print('        ({:.0%} done)'.format(i / len(all_pubs)))
        pub_path = PUBS_FOLDER.joinpath(pub_key)

        all_imgs = list(sorted(os.listdir(pub_path)))
        if len(all_imgs) > 1:
            print(f'Warning: pub {pub_key} was improperly packed (has {len(all_imgs)} images). Using first image.')
        if len(all_imgs) > 0:
            img_name = all_imgs[0]
            img_path = pub_path.joinpath(img_name)
            z.write(img_path, pub_key + '/' + img_name)
        else:
            print(f'Warning: pub {pub_key} was improperly packed (has no images). Skipping.')


def unpack():
    '''
    Unpack a gallery.zip file into the images directory for publications
    '''
    print('Unpacking...')
    # unpack gallery.zip file into images publications folder
    z = zipfile.ZipFile(GALLERY_ZIP, 'r')
    names = set(z.namelist())
    existing = {Path(pub_key).joinpath(img).as_posix() for pub_key in os.listdir(PUBS_FOLDER) for img in os.listdir(PUBS_FOLDER.joinpath(pub_key))}
    difference = names - existing

    z.extractall(PUBS_FOLDER)
    print(f'    - extracted {len(names)} files from gallery ({len(difference)} new)')

def extract_images():
    '''
    Extract all images from every new publication in the Zotero database and
    place all images in the images/* folder.
    '''
    # if main zotero database doesn't exist, pull from the zotero directory
    if not ZOTERO_GALLERY_DB.exists() or not BBT_GALLERY_DB.exists():
        pull(False)

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
            image_path = PUBS_FOLDER.joinpath(bbt_key)
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
                    if not image_path.exists():
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
        g.zotero_db = sqlite3.connect('file:' + str(ZOTERO_GALLERY_DB) + '?mode=ro', uri=True)
    return g.zotero_db

# Better BibTeX database
def get_bbt_db():
    db = getattr(g, 'bbt_db', None)
    if db is None:
        g.bbt_db = sqlite3.connect('file:' + str(BBT_GALLERY_DB) + '?mode=ro', uri=True)
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

    pub_keys = os.listdir(PUBS_FOLDER)
    publications = {}
    for pub_key in pub_keys:
        pub_data = {}
        # Query gallery db for information (`zoteroItemID`, `previewImageIndex`)
        gallery_result = cur_gallery.execute(f'SELECT zoteroItemID, previewImageIndex FROM gallery WHERE itemKey = "{pub_key}"')
        zotero_id, preview_index = gallery_result.fetchone()
        pub_data['zoteroItemID'] = zotero_id
        pub_data['previewImageIndex'] = preview_index

        # Get `images` list
        pub_folder = PUBS_FOLDER.joinpath(pub_key)
        img_list = []
        for img in sorted(os.listdir(pub_folder)):
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
    out_folder = PUBS_FOLDER.joinpath(entry_key)
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
pull:       pull databases from Zotero and make a backup in case something goes wrong.
push:       push databases to Zotero and make a backup in case something goes wrong.
pack:       pack all images into a single zip file and get rid of all images
            that aren't the single one we're displaying on the gallery.
unpack:     unpack gallery.zip file into the images folder
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

    elif 'pull' in sys.argv:
        pull()
        exit(0)

    elif 'push' in sys.argv:
        push()
        exit(0)

    elif 'pack' in sys.argv:
        pack()
        exit(0)

    elif 'unpack' in sys.argv:
        unpack()
        exit(0)

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
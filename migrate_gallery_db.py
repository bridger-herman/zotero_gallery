# migrate the gallery database to new format using bibtex key instead of itemID

import sys
import sqlite3
from app import GALLERY_DATA_DIR

if len(sys.argv) != 3:
    print('usage: python3 migrate_gallery_db.py <input database file> <output database file>')
    exit(1)

input_file = GALLERY_DATA_DIR.joinpath(sys.argv[1])
output_file = GALLERY_DATA_DIR.joinpath(sys.argv[2])

input_db = sqlite3.connect('file:' + str(input_file) + '?mode=ro', uri=True)
output_db = sqlite3.connect(output_file)

input_cur = input_db.cursor()
output_cur = output_db.cursor()

# create output gallery table
try:
    output_cur.execute('DROP TABLE gallery')
except sqlite3.OperationalError:
    pass
output_cur.execute('CREATE TABLE gallery (itemBibTexKey TEXT PRIMARY KEY NOT NULL, previewImageIndex INT DEFAULT 0);')

# select all existing entries from table
gallery_result = input_cur.execute(f'SELECT itemKey, previewImageIndex, zoteroItemID FROM gallery')
for bibtex_key, preview_image_index, item_id in gallery_result.fetchall():
    # migrate them one at a time
    output_cur.execute(f'INSERT INTO gallery (itemBibTexKey, previewImageIndex) VALUES ("{bibtex_key}", {preview_image_index});')
    print('Migrated', bibtex_key, preview_image_index)
output_db.commit() 

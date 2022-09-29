import os
import base64
from bs4 import BeautifulSoup as BS

# Convert types to file extensions
TYPES_TO_EXTENSIONS = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/svg+xml': '.svg',
    'image/gif': '.gif'
}

# Skip an entry and don't extract images if the html title contains
SKIP_TITLE_CONTAINS = {
    'Wiley Online Library',
    'IEEE Xplore'
}

def extract_html_images(imgdir, fname):
    with open(fname, 'rb') as fin:
        html = fin.read()
        soup = BS(html, features='lxml')
        for i, img_tag in enumerate(soup.find_all('img')):
            img = img_tag.attrs['src']
            type_start = img.find(':') + 1
            type_end = img.find(';')
            header_end = img.find(',')

            file_type = img[type_start:type_end]

            key = f'image_{i}'
            if 'alt' in img_tag.attrs and len(img_tag.attrs['alt']) > 0:
                key = img_tag.attrs['alt']

            try:
                filename = key + TYPES_TO_EXTENSIONS[file_type]
            except KeyError:
                print('extract_html_images WARNING: unable to find type', file_type, '. Skipping.')

            out_path = os.path.join(imgdir, filename)
            with open(out_path, 'wb') as fout:
                b64bytes = base64.b64decode(img[header_end + 1:])
                fout.write(b64bytes)
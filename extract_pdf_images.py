# Source:
# https://github.com/pymupdf/PyMuPDF-Utilities/blob/master/examples/extract-imga.py

from __future__ import print_function

import os
import time

import fitz

"""
PyMuPDF utility
----------------
For a given entry in a page's get_images() list, function "recoverpix"
returns a dictionary like the one produced by "Document.extract_image".
It preprocesses the following special cases:
* The PDF image has an /SMask (soft mask) entry. We use Pillow for recovering
  the original image with an alpha channel in RGBA format.
* The PDF image has a /ColorSpace definition. We then convert the image to
  an RGB colorspace.

The main script part implements the following features:
- prevent multiple extractions of same image
- prevent extraction of "unimportant" images, like "too small", "unicolor",
  etc. This can be controlled by parameters.

Apart from above special cases, the script aims to extract images with
their original file extensions. The produced filename is "img<xref>.<ext>",
with xref being the PDF cross reference number of the image.

Dependencies
------------
PyMuPDF v1.18.18

Changes
-------
* 2021-09-17: remove PIL and use extended pixmap features instead
* 2020-10-04: for images with an /SMask, we use Pillow to recover original
* 2020-11-21: convert cases with special /ColorSpace definitions to RGB PNG

"""

if not tuple(map(int, fitz.version[0].split("."))) >= (1, 18, 18):
    raise SystemExit("require PyMuPDF v1.18.18+")

dimlimit = 0  # 100  # each image side must be greater than this
relsize = 0  # 0.05  # image : image size ratio must be larger than this (5%)
abssize = 0  # 2048  # absolute image size limit 2 KB: ignore if smaller

def recoverpix(doc, item):
    xref = item[0]  # xref of PDF image
    smask = item[1]  # xref of its /SMask

    # special case: /SMask or /Mask exists
    if smask > 0:
        pix0 = fitz.Pixmap(doc.extract_image(xref)["image"])
        mask = fitz.Pixmap(doc.extract_image(smask)["image"])
        pix = fitz.Pixmap(pix0, mask)
        if pix0.n > 3:
            ext = "pam"
        else:
            ext = "png"

        return {  # create dictionary expected by caller
            "ext": ext,
            "colorspace": pix.colorspace.n,
            "image": pix.tobytes(ext),
        }

    # special case: /ColorSpace definition exists
    # to be sure, we convert these cases to RGB PNG images
    # if "/ColorSpace" in doc.xref_object(xref, compressed=True):
    #     pix = fitz.Pixmap(doc, xref)
    #     pix = fitz.Pixmap(fitz.csRGB, pix)
    #     return {  # create dictionary expected by caller
    #         "ext": "png",
    #         "colorspace": 3,
    #         "image": pix.tobytes("png"),
    #     }
    return doc.extract_image(xref)


def extract_pdf_images(imgdir, fname):
    t0 = time.time()
    doc = fitz.open(fname)

    page_count = doc.page_count  # number of pages

    xreflist = []
    imglist = []
    for pno in range(page_count):
        il = doc.get_page_images(pno)
        imglist.extend([x[0] for x in il])
        for img in il:
            xref = img[0]
            if xref in xreflist:
                continue
            width = img[2]
            height = img[3]
            if min(width, height) <= dimlimit:
                continue
            image = recoverpix(doc, img)
            n = image["colorspace"]
            imgdata = image["image"]

            if len(imgdata) <= abssize:
                continue
            if len(imgdata) / (width * height * n) <= relsize:
                continue

            imgfile = os.path.join(imgdir, "img%05i.%s" % (xref, image["ext"]))
            fout = open(imgfile, "wb")
            fout.write(imgdata)
            fout.close()
            xreflist.append(xref)

    t1 = time.time()
    return list(set(imglist))
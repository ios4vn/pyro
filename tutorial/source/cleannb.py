import argparse
import io  # for py2/py3 compatible

import nbformat


def cleannb(nbfile):
    with io.open(nbfile, "r", encoding="utf8") as f:
        nb = nbformat.read(f, as_version=nbformat.NO_CONVERT)

    nb["metadata"]["kernelspec"]["display_name"] = "Python 2"
    nb["metadata"]["kernelspec"]["name"] = "python2"
    nb["metadata"]["language_info"]["codemirror_mode"]["version"] = 2
    nb["metadata"]["language_info"]["pygments_lexer"] = "ipython2"
    nb["metadata"]["language_info"]["version"] = "2.7.14"

    with io.open(nbfile, "w", encoding="utf8") as f:
        nbformat.write(nb, f)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean kernelspec metadata of a notebook")
    parser.add_argument("nbfiles", nargs="*", help="Files to clean kernelspec metadata")
    args = parser.parse_args()

    for nbfile in args.nbfiles:
        cleannb(nbfile)

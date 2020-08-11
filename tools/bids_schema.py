#!/usr/bin/env python3
import argparse
from itertools import chain
import logging
import os
from pathlib import Path
import sys
from warnings import warn

import numpy as np
import pandas as pd
import yaml


#
# Aux utilities
#
def is_interactive():
    """Return True if all in/outs are tty"""
    # TODO: check on windows if hasattr check would work correctly and add
    # value:
    return sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty()


def setup_exceptionhook(ipython=False):
    """Overloads default sys.excepthook with our exceptionhook handler.

       If interactive, our exceptionhook handler will invoke
       pdb.post_mortem; if not interactive, then invokes default handler.
    """

    def _pdb_excepthook(type, value, tb):
        import traceback

        traceback.print_exception(type, value, tb)
        print()
        if is_interactive():
            import pdb

            pdb.post_mortem(tb)

    if ipython:
        from IPython.core import ultratb

        sys.excepthook = ultratb.FormattedTB(
            mode="Verbose",
            # color_scheme='Linux',
            call_pdb=is_interactive(),
        )
    else:
        sys.excepthook = _pdb_excepthook


def get_logger(name=None):
    """Return a logger to use
    """
    return logging.getLogger("bids-schema" + (".%s" % name if name else ""))


def set_logger_level(lgr, level):
    if isinstance(level, int):
        pass
    elif level.isnumeric():
        level = int(level)
    elif level.isalpha():
        level = getattr(logging, level)
    else:
        lgr.warning("Do not know how to treat loglevel %s" % level)
        return
    lgr.setLevel(level)


_DEFAULT_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

lgr = get_logger()
# Basic settings for output, for now just basic
set_logger_level(lgr, os.environ.get("BIDS_SCHEMA_LOG_LEVEL", logging.INFO))
FORMAT = "%(asctime)-15s [%(levelname)8s] %(message)s"
logging.basicConfig(format=FORMAT)

BIDS_SCHEMA = Path(__file__).parent.parent / "src" / "schema"


def _get_entry_name(path):
    if path.suffix == '.yaml':
        return path.name[:-5]  # no .yaml
    else:
        return path.name


def _get_parser():
    """
    Parses command line inputs for NiMARE

    Returns
    -------
    parser.parse_args() : argparse dict
    """
    parser = argparse.ArgumentParser(prog='bids')
    subparsers = parser.add_subparsers(help='BIDS workflows')
    # show()
    show_parser = subparsers.add_parser(
        'show',
        help=('Print out the schema'),
    )
    show_parser.set_defaults(func=show)
    show_parser.add_argument(
        'schema_path',
        type=Path,
        help=('Path to schema to show.')
    )
    # entity_table()
    entity_parser = subparsers.add_parser(
        'entity',
        help=('Print entity table')
    )
    entity_parser.set_defaults(func=save_entity_table)
    entity_parser.add_argument(
        'schema_path',
        type=Path,
        help=('Path to schema to show.')
    )
    entity_parser.add_argument(
        'out_file',
        type=str,
        help=('Output filename.')
    )
    return parser


def load_schema(schema_path):
    """The schema loader

    It allows for schema, like BIDS itself, to be specified in
    a hierarchy of directories and files.
    File (having .yaml stripped) and directory names become keys
    in the associative array (dict) of entries composed from content
    of files and entire directories.

    Parameters
    ----------
    schema_path : str
        Folder containing yaml files or yaml file.

    Returns
    -------
    dict
        Schema in dictionary form.
    """
    schema_path = Path(schema_path)
    if schema_path.is_file() and (schema_path.suffix == '.yaml'):
        with open(schema_path) as f:
            return yaml.load(f, Loader=yaml.SafeLoader)
    elif schema_path.is_dir():
        # iterate through files and subdirectories
        res = {
            _get_entry_name(path): load_schema(path)
            for path in sorted(schema_path.iterdir())
        }
        return {k: v for k, v in res.items() if v is not None}
    else:
        warn(f"{schema_path} is somehow nothing we can load")


def show(schema_path):
    """Print full schema."""
    schema = load_schema(schema_path)
    print(yaml.safe_dump(schema, default_flow_style=False))


def drop_unused_entities(df):
    df = df.replace('', np.nan).dropna(axis=1, how='all').fillna('')
    return df


def flatten_multiindexed_columns(df):
    # Flatten multi-index
    vals = df.index.tolist()
    df.loc['Format'] = df.columns.get_level_values(1)
    df.columns = df.columns.get_level_values(0)
    df = df.loc[['Format'] + vals]
    df.index.name = 'Entity'
    df = df.drop(columns=['DataType'])
    return df


def make_entity_table(schema_path):
    """Produce entity table (markdown) based on schema.
    This only works if the top-level schema *directory* is provided.

    Parameters
    ----------
    schema_path : str
        Folder containing schema, which is stored in yaml files.

    Returns
    -------
    table : pandas.DataFrame
        DataFrame of entity table, with two layers of columns.
    """
    schema = load_schema(schema_path)

    # prepare the table based on the schema
    # import pdb; pdb.set_trace()
    header = ['Entity', 'DataType']
    formats = ['Format', 'DataType']
    entity_to_col = {}
    table = [formats]

    # Compose header and formats first
    for i, (entity, spec) in enumerate(schema['entities'].items()):
        header.append(spec["name"])
        formats.append(f'`{entity}-<{spec["format"]}>`')
        entity_to_col[entity] = i + 1

    # Go through data types
    for dtype, specs in chain(schema['datatypes'].items(),
                              schema['auxdatatypes'].items()):
        dtype_rows = {}

        # each dtype could have multiple specs
        for spec in specs:
            # datatypes use suffixes, while
            # for auxdatatypes we need to use datatypes
            # TODO: RF to avoid this guesswork
            suffixes = spec.get('datatypes') or spec.get('suffixes')
            # TODO: <br> is specific for html form
            suffixes_str = ' '.join(suffixes) if suffixes else ''
            dtype_row = [dtype] + ([''] * len(entity_to_col))
            for ent, req in spec.get('entities', []).items():
                dtype_row[entity_to_col[ent]] = req.upper()

            # Merge specs within dtypes if they share all of the same entities
            if dtype_row in dtype_rows.values():
                for k, v in dtype_rows.items():
                    if dtype_row == v:
                        dtype_rows.pop(k)
                        new_k = k + ' ' + suffixes_str
                        new_k = new_k.strip()
                        dtype_rows[new_k] = v
                        break
            else:
                dtype_rows[suffixes_str] = dtype_row

        # Reformat first column
        dtype_rows = {dtype+'<br>({})'.format(k): v for k, v in
                      dtype_rows.items()}
        dtype_rows = [[k] + v for k, v in dtype_rows.items()]
        table += dtype_rows

    # Create multi-level index because first two rows are headers
    cols = list(zip(header, table[0]))
    cols = pd.MultiIndex.from_tuples(cols)
    table = pd.DataFrame(data=table[1:], columns=cols)
    table = table.set_index(('Entity', 'Format'))

    # Now we can split as needed, in the next function
    return table


def make_entity_table_markdown(schema_path, tablefmt='github'):
    """
    Create a tabulated entity table from the schema.

    This only works if the top-level schema *directory* is provided.

    Parameters
    ----------
    schema_path : str
        Path to schema.
    tablefmt : {'github'}, optional
        Format for tabulated table.

    Returns
    -------
    out_tables : dict
        Dictionary of tabulated entity tables, with table title as key.
    """
    from tabulate import tabulate
    table = make_entity_table(schema_path)

    # Split table
    EG_DATATYPES = ['eeg', 'ieeg', 'meg', 'channels', 'electrodes', 'events',
                    'photo']
    MRI_DATATYPES = ['anat', 'func', 'fmap', 'dwi']
    mri_table = table.loc[
        table[('DataType', 'DataType')].isin(MRI_DATATYPES)
    ]
    eg_table = table.loc[
        table[('DataType', 'DataType')].isin(EG_DATATYPES)
    ]
    beh_table = table[
        ~table[('DataType', 'DataType')].isin(MRI_DATATYPES + EG_DATATYPES)
    ]

    out_tables = {}
    titles = [
        '## Magnetic Resonance Imaging',
        '## Encephalography (EEG, iEEG, and MEG)',
        '## Behavioral Data'
    ]
    tables = [mri_table, eg_table, beh_table]
    for i, table in enumerate(tables):
        title = titles[i]
        table = drop_unused_entities(table)
        table = flatten_multiindexed_columns(table)
        # print it as markdown
        table_str = tabulate(table, headers='keys', tablefmt=tablefmt)
        out_tables[title] = table_str

    return out_tables


def save_entity_table(schema_path, out_file):

    tables = make_entity_table_markdown(schema_path)

    intro_text = """\
# Appendix IV: Entity table

This section compiles the entities (key-value pairs) described throughout this
specification, and establishes a common order within a filename. 
For example, if a file has an acquisition and reconstruction label, the
acquisition entity must precede the reconstruction entity.
REQUIRED and OPTIONAL entities for a given file type are denoted.
Entity formats indicate whether the value is alphanumeric
(`<label>`) or numeric (`<index>`).

A general introduction to entities is given in the section on
[file name structure](../02-common-principles.md#file-name-structure)
"""
    with open(out_file, 'w') as fo:
        fo.write(intro_text)
        fo.write('\n')
        for i, (title, table) in enumerate(tables.items()):
            fo.write(title)
            fo.write('\n\n')
            fo.write(table)
            if i == len(tables) - 1:
                fo.write('\n')
            else:
                fo.write('\n\n')


def _main(argv=None):
    """BIDS schema CLI entrypoint.

    Examples
    --------
    >>> python bids_schema.py entity ../src/schema/ \
    >>> ../src/99-appendices/04-entity-table.md
    """
    options = _get_parser().parse_args(argv)
    args = vars(options).copy()
    args.pop('func')
    options.func(**args)


if __name__ == '__main__':
    _main()

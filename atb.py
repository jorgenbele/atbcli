#!/usr/bin/env python
#
# Date: 05.04.2019
# Author: Jørgen Bele Reinfjell
# Description: AtB route plannner cli to get next departure

"""
Usage: atb [(<from_stop> <to_stop>) | <saved_route>] [options]

Options:
  --pretty
  --terse
  -v, --verbose                  Enable verbose output
  --no-suggestions               Disable the use of AtB bus-stop suggestions (on by default when --to or --from is provided)
  --routes-file <routes_file>    Use a custom route file (default: ~/.config/atb/routes.json)
  --from <from_stop>             Alternative way to specify, or override 'from' bus-stop.
  --to <to_stop>                 Alternative way to specify, or override 'to' bus-stop.


Note:
   If <saved_route> is provided as well as <from_stop> and/or <to_stop>
   then one can override the from/to field of the <saved_route>.
"""
from pathlib import Path
import arrow
from bs4 import BeautifulSoup as BS
import requests
from docopt import docopt
import sys
import json
from iterfzf import iterfzf

DEFAULT_FROM_BUS_STOP='Munkegata M4 (Trondheim)'
DEFAULT_TO_BUS_STOP='Solsiden (Trondheim)'

DEFAULT_ROUTES_FILE='~/.config/atb/routes.json'

### FROM confs/common.py
def is_interactive():
    return sys.__stdout__.isatty()

class ArgFlags:
    pretty = False
    pretty_or_terse_flag_present = False
    verbose = False
    interactive = False

    @staticmethod
    def from_args(args):
        ArgFlags.interactive = is_interactive()
        for arg in [k for k, v in args.items() if v]: # only care about flags set to True
            if arg == '--pretty':
                ArgFlags.pretty = True
                ArgFlags.pretty_or_terse_flag_present = True
            elif arg == '--terse':
                ArgFlags.pretty = False
                ArgFlags.pretty_or_terse_flag_present = True
            elif arg == '--verbose':
                ArgFlags.verbose = True

        if not ArgFlags.pretty_or_terse_flag_present:
            # use --pretty implicitly when interactive
            ArgFlags.pretty = ArgFlags.interactive

# MODIFIED: added 'arch' flag
def pprint(fargs, *args, word_wrap=True, warning=False, bold=False, success=False, enabled=False, fatal=False, header=False, arch=False, **kwargs):
    def escape(s):
        return '\033{}'.format(s)

    """Pretty print"""
    if ArgFlags.pretty:
        bold_str = escape('[1m') if bold else ''
        color_str = ''
        reset_str = ''
        if warning or fatal:
            color_str = escape('[31m') # red
        elif success or enabled:
            color_str = escape('[32m') # green
        elif header:
            color_str = escape('[33m') # orange
        elif arch:
            color_str = escape('[35m') # magenta


        if bold or len(color_str) > 0:
            reset_str = escape('[0m') # none

        # TODO wordwrap
        nfargs = '{}{}{}'.format(bold_str, color_str, fargs)
        return print(nfargs, *args, reset_str, **kwargs)
    else:
        return print(fargs, *args, **kwargs)

def stringify(row):
    return [str(c) for c in row]

def align_columns(rows, column_options=None, spacing=1, header=None):
    """
    Aligns columns in a row of items.
    column_options is a list containing descibing how each column
    should be aligned
    Example: ['left', 'center', 'right'] will align the first column to the
    left, the second to the center, and the third to the right.
    """

    srows = []
    if header:
        srows.append(stringify(header))
    srows = srows + [stringify(row) for row in rows]

    column_details = {}
    for r in srows:
        for i, c in enumerate(r):
            if i not in column_details:
                column_details[i] = {'width': 0}
            column_details[i]['width'] = max(column_details[i]['width'], len(c))

    spacing_str = ' ' * spacing

    def align_row(row):
        nonlocal column_details, column_options
        out_columns = []
        for i, c in enumerate(row):
            align_chr = '<' # left-align by default
            if column_options:
                if column_options[i] == 'right':
                        align_chr = '>'
                elif column_options[i] == 'center':
                        align_chr = '^'
            out_columns.append('{0:{1}{2}}'.format(c, align_chr, column_details[i]['width']))
        return spacing_str.join(out_columns)

    out = []
    for r in srows:
        out.append(align_row(r))
    return out


def print_rows(rows, column_options=None, spacing=1, header=None, enabled_rows=set(), **kwargs):
    # TODO **kwargs
    # Only print header if pretty
    if len(rows) < 1:
        return False

    if ArgFlags.pretty:
        aligned = align_columns(rows=rows, column_options=column_options, spacing=spacing, header=header)
        #print(aligned)
        if header:
            #### MODIFIED : header=True => arch=True, for the maximum arch like experience
            pprint(aligned[0], arch=True, bold=True, **kwargs)
            aligned.pop(0)
        for i, row in enumerate(aligned):
            pprint(row, enabled=(i in enabled_rows), **kwargs)
    else:
        for row in rows:
            print('{}'.format(' '.join(stringify(row))))

    return True
### END OF COPY

def busstop_suggestions(query):
    """
    Queries the AtB travel planner service for bus stops. This utilizes
    the autocomplete functionality found on the site.

    The AtB travel planner service returns a json document containing
    the following:
    {
       'query': <param query>,
       'suggestions': <list of suggested bus stops>
    }

    Returns the 'suggestions' field.
    """
    # This endpoint takes a GET request with a 'query' field and returns a
    # list of possible autocompletions.
    base_url = 'https://rp.atb.no/scripts/TravelMagic/TravelMagicWE.dll/StageJSON'

    payload = { 'query': query }
    r = requests.get(base_url, params=payload)
    return r.json().get('suggestions', [])

def get_departures(direction=1, from_stop=DEFAULT_FROM_BUS_STOP, to_stop=DEFAULT_TO_BUS_STOP, time_str=None, date_str=None):
    """
    Queries the AtB travel planner service for bus routes specified by the
    function parameters.

    Keyword arguments:
    direction -- used by the travel planner service, unknown function
    from_stop -- the bus stop to travel from
    to_stop   -- the bus stop to travel to
    date_str  -- a string on the form DD.MM.YYYY representing the date of the travel occours on
    time_str  -- a string on the form HH:MM representing the hour and minute of on the date the travel occours on
    """

    #base_url = 'https://atb.no/reiseplanlegger'
    # They use a iframe containing the following url, so use that instead:
    base_url = 'https://rp.atb.no/scripts/TravelMagic/TravelMagicWE.dll/svar'

    now = arrow.now()
    if not time_str:
        time_str = f'{now.hour}:{now.minute}'
    if not date_str:
        date_str =  f'{now.day}.{now.month}.{now.year}'

    payload = {
        'direction': direction,
        'from': from_stop,
        'to': to_stop,
        'time': time_str,
        'date': date_str,
        'search': 'Show travel suggestions'
    }
    r = requests.get(base_url, params=payload)
    return r.text

def parse_departures(resp_html):
    """
    Parses and returns a response from the travel planner service
    which can be achieved by callig get_departures().

    Returns a list of tuples on the form: (start_time, duration, travel_list),
    where travel_list is a list of strings representing the different
    ways of travel. These can be: a bus number (as string) or 'walking'
    if walking is needed to get to the destination.

    Example: [('13:51', '0:10', ['38', 'walking']), ('13:55', '0:20', ['66', '36', '66'])]
    """
    b = BS(resp_html, features='lxml')
    result_wrappers = b.findAll('div', {'class': 'tm-result-wrapper'})

    res = []
    for rw in result_wrappers:
        start_span = rw.findNext('span', {'class': 'tm-result-fratil'})
        duration_span = rw.findNext('span', {'class': 'tm-result-value-time'}).findNext('span', {'class': 'tm-result-info-val'})
        dets_spans = rw.findAll('span', {'class': 'tm-det'})
        dets = []
        for ds in dets_spans:
            linenr_span = ds.findNext('span', {'class': 'tm-det-linenr'})
            if linenr_span:
                dets.append(linenr_span.text)
            else:
                # assume that it will be 'walking'
                dets.append('walking')

        # cleanup dets
        res.append((start_span.text, duration_span.text, dets))

    return res

def single_or_fzf(options):
    """
    If there are more than one element in options
    make the user specify one using fzf, otherwise
    return the single element.
    """
    if len(options) == 1:
        return options[0]
    else:
        return iterfzf(options)

def main(argv):
    args = docopt(__doc__, argv)
    #print(args)
    ArgFlags.from_args(args)
    if ArgFlags.verbose:
        print(args)

    routes_file = Path(DEFAULT_ROUTES_FILE).expanduser()
    if args.get('--routes-file', None):
        routes_file = Path(args['--routes-file']).expanduser()

    r_to, r_from = None, None
    use_suggestions = True

    # NOTE: Can override parts of the saved route when specifying --to/--from.
    if args.get('<saved_route>', None):
        saved_route = args['<saved_route>']
        routes = {}
        try:
            routes = json.load(open(routes_file))
        except Exception as e:
            print(f'Failed to load routes file {routes_file}: {e}', file=sys.stderr)
            exit(1)

        if routes.get(saved_route, None):
            r_to = routes[saved_route]['to']
            r_from = routes[saved_route]['from']
            use_suggestions = False # Assume that saved routes are correct
        else:
            print(f'Route {saved_route} not found in {routes_file}', file=sys.stderr)
            exit(2)

    if args.get('--to', None): # --to <to_stop>
        r_to = args['--to']
    elif args.get('<to_stop>', None): # atb <from_stop> <to_stop>
        r_to = args['<to_stop>']

    if args.get('--from', None): # --from <from_stop>
        r_from = args['--from']
    elif args.get('<from_stop>', None): # atb <from_stop> <to_stop>
        r_from = args['<from_stop>']

    # Assumption: user has provided to and from bus-stop.
    if use_suggestions and not args.get('--no-suggestions', False):
        r_to = single_or_fzf(busstop_suggestions(r_to))
        r_from = single_or_fzf(busstop_suggestions(r_from))

    response = get_departures(to_stop=r_to, from_stop=r_from)
    departures = [[d, t, ' \t->  '.join(r)] for d, t, r in parse_departures(response)]

    pprint(':: AtB', bold=True)
    pprint(f':: From {r_from} to {r_to}', bold=True)
    header = ['Departure', 'Duration', 'Route']
    #rows = [[i.pubDate.text, i.title.text, i.link.text] for i in items[:int(args['--limit'] or -1)]]
    print_rows(departures, spacing=3, header=header)

if __name__ == '__main__':
    main(sys.argv[1:])

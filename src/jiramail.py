#! /usr/bin/python
#
# Originally written by Jan Schaumann <jschauma@netmeister.org> in August
# 2012.
#
# This script reads an SMTP message from stdin and appends the body as a
# comment to the Jira ticket found in the 'Subject: ' header using the
# Jira API.

import getopt
import getpass
import email
import json
import os
import re
import stat
import sys
import urllib
import urllib2

from cookielib import CookieJar

###
### GLOBALS
###

COOKIEJAR = CookieJar()
OPENER = None

CONFIG = {
    "API" : "/rest/api/latest/issue/",
    "AUTH" : "/rest/auth/latest/session",
    # The comment to append to the tickets, populated by 'parseInput'
    "COMMENT" : "",
    "CFGFILE" : os.path.expanduser("~/.jiramailrc"),
    # If the '-d' option is given, don't do anything.
    "DONT" : False,
    # The user to submit the comment as, populated from either the config
    # file or, if not set there, from the input ("From: ")
    "FROM" : None,
    # API host, from config file
    "HOST" : None,
    # Address from which ticket updates are coming, ie what we'd be
    # replying to.
    "JIRA" : None,
    # Ugh. I don't want to encourage anybody to store their password in a
    # plain text file, but forgive me, this is probably the single most
    # convenient thing you can do to make this tool useful (well, besides
    # implementing OAuth on the jira server, but apparently Jira does not
    # allow per-user oauth. Boo!).
    "JIRA_PASS" : "",
    # We normally act as a filter and print the incoming message after
    # processing it.  The '-s' option can be used to disable this.
    "SWALLOW" : False,
    # The tickets found in the input, populated by 'parseInput'
    "TICKETS" : []
    }

VERBOSITY = 0

###
### Functions
###

# get a valid cookie for Jira
# returns void; the global OPENER will be a urllib2 opener associated with
# a valid cookie
def getCookie():
    global OPENER
    verbose("Getting a cookie for %s..." % CONFIG["HOST"])

    cookie = None
    login_url = CONFIG["HOST"] + CONFIG["AUTH"]
    username = CONFIG["FROM"]

    password = getPassword()

    verbose("Logging into %s as %s..." % (login_url, username), 2)

    data = '{ "username" : "%s" , "password" : "%s" }' % (username, password)
    headers = { 'Content-Type' : 'application/json',
                'Accept' : 'application/json' }

    OPENER = urllib2.build_opener(urllib2.HTTPCookieProcessor(COOKIEJAR))
    request = urllib2.Request(login_url, data, headers)

    if not CONFIG["DONT"]:
        makeRequest(request)


# make a request using the global OPENER
def makeRequest(request):
    try:
        response = OPENER.open(request)
    except urllib2.URLError, e:
        sys.stderr.write("Unable to request '%s'.\n" % request.get_full_url())
        sys.stderr.write(str(e) + "\n")
        sys.exit(1)
        # NOTREACHED

    verbose("Response headers:\n%s" % response.info(), 4)
    verbose("Response content:\n%s" % response.read(), 4)
    response.close()


# get the password to use; we try the following:
# - check if it was set in the config file (oy!)
# - check if it was set in the environment (smaller oy!)
# - prompt the user (better, but less convenient)
def getPassword():
    verbose("Getting password...", 2)

    password = CONFIG["JIRA_PASS"]

    if not password and os.environ.has_key("JIRA_PASS"):
        password = os.environ["JIRA_PASS"]

    if not password:
        password = getpass.getpass()

    return password


# parse the given configuration file and set the appropriate variables
def parseConfig(cfg):
    global CONFIG
    verbose("Reading configuration from %s..." % CONFIG["CFGFILE"])

    try:
        sb = os.stat(cfg)
        m = stat.S_IMODE(sb.st_mode)
        if m > int("600", 8):
            sys.stderr.write("Refusing to read config file '%s' with unsafe permissions.\n" % cfg)
            sys.stderr.write("Please set mode to 0600 or less (currently: %o).\n" % m)
            sys.exit(1)
    except IOError, e:
        sys.stderr.write("Unable to stat %s: %s\n" % (cfg, os.strerror(e.errno)))
        sys.exit(1)
        # NOTREACHED

    comments = re.compile("#.*")
    field_re = re.compile("\s*(?P<field>\S*)\s*=\s*(?P<value>.*)")
    try:
        f = open(cfg, "r")
        for line in f.readlines():
            line = re.sub(comments, '', line)
            m = field_re.match(line)
            if m:
                field = m.group("field")
                value = m.group("value")
                if (field == "HOST") and (value.find("http") != 0):
                    value = "https://" + value
                CONFIG[field] = value.rstrip()
        f.close()
    except IOError, e:
        sys.stderr.write("Unable to read %s: %s\n" % (cfg, os.strerror(e.errno)))
        sys.exit(1)
        # NOTREACHED


# parse stdin, which we expect to be an SMTP message (including headers)
# extract:
#  From: -- if no USER is set, use this username
#  Subject: -- if any FOO-123 strings are found, use those as tickets
#  Body -- this becomes the comment to be appended
#
# Returns: an email message identical to the input with the exception that
# the jira address is removed from the to/cc/bcc field
def parseInput():
    global CONFIG

    verbose("Parsing input...")

    msg = email.message_from_file(sys.stdin)

    msg_to = msg.get("To")
    msg_cc = msg.get("Cc")
    msg_bcc = msg.get("Bcc")
    jira_pattern = "([\s<,]%s[>,]?)" % CONFIG["JIRA"]
    jira_mail = False

    for field in "To", "Cc", "Bcc":
        header = msg.get(field)
        if header:
            m = re.search(jira_pattern, header, re.I)
            if m:
                jira_mail = True
                header = re.sub(jira_pattern, "", header, re.I)
                msg.replace_header(field, header)
                # If there's only one recipient, say "Jira <jira@example.com>",
                # then our replacing yielded "Jira ", meaning we don't
                # have a valid "To" at all, so nuke it:
                if not "@" in msg.get(field):
                    del(msg[field])
                break

    if jira_mail:
        if not CONFIG["FROM"]:
            CONFIG["FROM"] = re.sub(".*[\s<](.*?)@.*", r'\1', msg.get("From"))
            verbose("From: %s" % CONFIG["FROM"], 2)

        CONFIG["TICKETS"] = parseSubject(msg.get("Subject"))
        verbose("Tickets found in Subject: %s" % ", ".join(CONFIG["TICKETS"]), 2)

        if not msg.is_multipart():
            CONFIG["COMMENT"] = msg.get_payload()
        else:
            for m in msg.get_payload():
                if m.get_content_type() == "text/plain":
                    CONFIG["COMMENT"] = m.get_payload()
        verbose("Comment: %s" % CONFIG["COMMENT"], 3)
    else:
        verbose("Address (%s) does not match our Jira address (%s), ignoring." % \
                            (msg_to, CONFIG["JIRA"]))

    return msg


# parse command-line options and set appropriate flags
# returns void (ie globals are set)
def parseOptions(args):
    global CONFIG, VERBOSITY

    try:
        opts, args = getopt.getopt(args, "dc:hsv")
    except getopt.GetoptError, e:
        sys.stderr.write(e.msg + "\n")
        usage()
        sys.exit(1)
        # NOTREACHED

    for o, a in opts:
        if o in ("-d"):
            CONFIG["DONT"] = True
        if o in ("-c"):
            CONFIG["CFGFILE"] = a
        if o in ("-h"):
            usage()
            sys.exit(0)
            # NOTREACHED
        if o in ("-s"):
            CONFIG["SWALLOW"] = True
        if o in ("-v"):
            VERBOSITY += 1


# parse the subject line and extract any ticket references
# returns a list of strings ([ "FOO-123", "BAR-555" ])
def parseSubject(subject):
    verbose("Parsing subject line...", 2)
    verbose(subject.rstrip(), 3)

    tickets = {}

    ticket_re = re.compile("(?P<ticket>[A-Z]+-[0-9]+)")
    for match in ticket_re.finditer(subject):
        tickets[match.group("ticket")] = True

    return tickets.keys()


# update the given ticket
def updateTicket(ticket):
    global OPENER
    verbose("Updating ticket %s..." % ticket)

    api_url = CONFIG["HOST"] + CONFIG["API"] + ticket + "/comment"
    verbose(api_url, 2)

    headers = { 'Content-Type' : 'application/json',
                'X-Atlassian-Token': 'no-check'}

    data = { "body" : CONFIG["COMMENT"] }
    verbose("data : '%s'" % data, 3)

    request = urllib2.Request(api_url, json.dumps(data), headers)
    verbose("headers : ", 3)
    for n,v in request.header_items():
        verbose("%s : %s" % (n, v), 3)

    if not CONFIG["DONT"]:
        makeRequest(request)


# print a short usage message
def usage():
    progname = os.path.basename(sys.argv[0])
    sys.stdout.write("%s: list etsy admin logins from data centers\n" % progname)
    sys.stdout.write("Usage: %s [-dhsv] [-c file]\n"  % progname)
    sys.stdout.write("\t-c file  read config file (default: %s)\n" % CONFIG["CFGFILE"])
    sys.stdout.write("\t-d       don't do anything\n")
    sys.stdout.write("\t-h       print this message and exit\n")
    sys.stdout.write("\t-s       don't print the message, swalling it\n")
    sys.stdout.write("\t-v       be verbose\n")


def verbose(msg, threshold=1):
    if VERBOSITY >= threshold:
        i = 0
        while i < threshold:
            sys.stderr.write("=")
            i += 1
        sys.stderr.write("> %s\n" % msg)

###
### Main
###

parseOptions(sys.argv[1:])
parseConfig(CONFIG["CFGFILE"])
msg = parseInput()

if CONFIG["COMMENT"] and CONFIG["TICKETS"]:
    getCookie()
    for ticket in CONFIG["TICKETS"]:
        updateTicket(ticket)

if not CONFIG["SWALLOW"]:
    sys.stdout.write(msg.as_string(unixfrom=False))

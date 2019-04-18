# encoding=utf8
import datetime
from distutils.version import StrictVersion
import hashlib
import os.path
import random
from seesaw.config import realize, NumberConfigValue
from seesaw.externalprocess import ExternalProcess
from seesaw.item import ItemInterpolation, ItemValue
from seesaw.task import SimpleTask, LimitConcurrent
from seesaw.tracker import GetItemFromTracker, PrepareStatsForTracker, \
    UploadWithTracker, SendDoneToTracker
import shutil
import socket
import subprocess
import sys
import time
import string
import re

try:
    import warcio
    from warcio.archiveiterator import ArchiveIterator
    from warcio.warcwriter import WARCWriter
except:
    raise Exception("Please install warc with 'sudo pip install warcio --upgrade'.")

import seesaw
from seesaw.externalprocess import WgetDownload
from seesaw.pipeline import Pipeline
from seesaw.project import Project
from seesaw.util import find_executable

from tornado import httpclient


# check the seesaw version
if StrictVersion(seesaw.__version__) < StrictVersion("0.8.5"):
    raise Exception("This pipeline needs seesaw version 0.8.5 or higher.")


###########################################################################
# Find a useful Wget+Lua executable.
#
# WGET_LUA will be set to the first path that
# 1. does not crash with --version, and
# 2. prints the required version string
WGET_LUA = find_executable(
    "Wget+Lua",
    ["GNU Wget 1.14.lua.20130523-9a5c", "GNU Wget 1.14.lua.20160530-955376b"],
    [
        "./wget-lua",
        "./wget-lua-warrior",
        "./wget-lua-local",
        "../wget-lua",
        "../../wget-lua",
        "/home/warrior/wget-lua",
        "/usr/bin/wget-lua"
    ]
)

if not WGET_LUA:
    raise Exception("No usable Wget+Lua found.")


###########################################################################
# The version number of this pipeline definition.
#
# Update this each time you make a non-cosmetic change.
# It will be added to the WARC files and reported to the tracker.
VERSION = "20190311.01"
USER_AGENT = 'ArchiveTeam'
TRACKER_ID = 'flickr'
TRACKER_HOST = 'tracker.archiveteam.org'


###########################################################################
# This section defines project-specific tasks.
#
# Simple tasks (tasks that do not need any concurrency) are based on the
# SimpleTask class and have a process(item) method that is called for
# each item.
class CheckIP(SimpleTask):
    def __init__(self):
        SimpleTask.__init__(self, "CheckIP")
        self._counter = 0

    def process(self, item):
        # NEW for 2014! Check if we are behind firewall/proxy

        if self._counter <= 0:
            item.log_output('Checking IP address.')
            ip_set = set()

            ip_set.add(socket.gethostbyname('twitter.com'))
            ip_set.add(socket.gethostbyname('facebook.com'))
            ip_set.add(socket.gethostbyname('youtube.com'))
            ip_set.add(socket.gethostbyname('microsoft.com'))
            ip_set.add(socket.gethostbyname('icanhas.cheezburger.com'))
            ip_set.add(socket.gethostbyname('archiveteam.org'))

            if len(ip_set) != 6:
                item.log_output('Got IP addresses: {0}'.format(ip_set))
                item.log_output(
                    'Are you behind a firewall/proxy? That is a big no-no!')
                raise Exception(
                    'Are you behind a firewall/proxy? That is a big no-no!')

        # Check only occasionally
        if self._counter <= 0:
            self._counter = 10
        else:
            self._counter -= 1


class PrepareDirectories(SimpleTask):
    def __init__(self, warc_prefix):
        SimpleTask.__init__(self, "PrepareDirectories")
        self.warc_prefix = warc_prefix

    def process(self, item):
        item_name = item["item_name"]
        escaped_item_name = item_name.replace(':', '_').replace('/', '_').replace('~', '_')
        item_hash = hashlib.sha1(item_name.encode('utf-8')).hexdigest()
        dirname = "/".join((item["data_dir"], item_hash))

        if os.path.isdir(dirname):
            shutil.rmtree(dirname)

        os.makedirs(dirname)

        item["item_dir"] = dirname
        item["warc_file_base"] = "%s-%s-%s" % (self.warc_prefix, item_hash,
            time.strftime("%Y%m%d-%H%M%S"))

        open("%(item_dir)s/%(warc_file_base)s.warc" % item, "w").close()
        open("%(item_dir)s/%(warc_file_base)s_data.txt" % item, "w").close()


class Deduplicate(SimpleTask):
    def __init__(self):
        SimpleTask.__init__(self, "Deduplicate")

    def process(self, item):
        digests = {}
        input_filename = "%(item_dir)s/%(warc_file_base)s.warc" % item
        output_filename = "%(item_dir)s/%(warc_file_base)s-deduplicated.warc.gz" % item
        with open(input_filename, 'rb') as f_in, \
                open(output_filename, 'wb') as f_out:
            writer = WARCWriter(filebuf=f_out, gzip=True)
            for record in ArchiveIterator(f_in):
                url = record.rec_headers.get_header('WARC-Target-URI')
                if url is not None and url.startswith('<'):
                    url = re.search('^<(.+)>$', url).group(1)
                    record.rec_headers.replace_header('WARC-Target-URI', url)
                if record.rec_headers.get_header('WARC-Type') == 'response':
                    digest = record.rec_headers.get_header('WARC-Payload-Digest')
                    if digest in digests:
                        writer.write_record(
                            self._record_response_to_revisit(writer, record,
                                                             digests[digest])
                        )
                    else:
                        digests[digest] = (
                            record.rec_headers.get_header('WARC-Record-ID'),
                            record.rec_headers.get_header('WARC-Date'),
                            record.rec_headers.get_header('WARC-Target-URI')
                        )
                        writer.write_record(record)
                elif record.rec_headers.get_header('WARC-Type') == 'warcinfo':
                    record.rec_headers.replace_header('WARC-Filename', output_filename)
                    writer.write_record(record)
                else:
                    writer.write_record(record)

    def _record_response_to_revisit(self, writer, record, duplicate):
        warc_headers = record.rec_headers
        warc_headers.replace_header('WARC-Refers-To', duplicate[0])
        warc_headers.replace_header('WARC-Refers-To-Date', duplicate[1])
        warc_headers.replace_header('WARC-Refers-To-Target-URI', duplicate[2])
        warc_headers.replace_header('WARC-Type', 'revisit')
        warc_headers.replace_header('WARC-Truncated', 'length')
        warc_headers.replace_header('WARC-Profile',
                                    'http://netpreserve.org/warc/1.0/' \
                                    'revisit/identical-payload-digest')
        warc_headers.remove_header('WARC-Block-Digest')
        warc_headers.remove_header('Content-Length')
        return writer.create_warc_record(
            record.rec_headers.get_header('WARC-Target-URI'),
            'revisit',
            warc_headers=warc_headers,
            http_headers=record.http_headers
        )


class MoveFiles(SimpleTask):
    def __init__(self):
        SimpleTask.__init__(self, "MoveFiles")

    def process(self, item):
        os.rename("%(item_dir)s/%(warc_file_base)s-deduplicated.warc.gz" % item,
            "%(data_dir)s/%(warc_file_base)s-deduplicated.warc.gz" % item)
        os.rename("%(item_dir)s/%(warc_file_base)s_data.txt" % item,
            "%(data_dir)s/%(warc_file_base)s_data.txt" % item)

        shutil.rmtree("%(item_dir)s" % item)


def get_hash(filename):
    with open(filename, 'rb') as in_file:
        return hashlib.sha1(in_file.read()).hexdigest()


CWD = os.getcwd()
PIPELINE_SHA1 = get_hash(os.path.join(CWD, 'pipeline.py'))
LUA_SHA1 = get_hash(os.path.join(CWD, 'flickr.lua'))


def stats_id_function(item):
    # NEW for 2014! Some accountability hashes and stats.
    d = {
        'pipeline_hash': PIPELINE_SHA1,
        'lua_hash': LUA_SHA1,
        'python_version': sys.version,
    }

    return d


class WgetArgs(object):
    def realize(self, item):
        wget_args = [
            WGET_LUA,
            "-U", USER_AGENT,
            "-nv",
            "--no-cookies",
            "--lua-script", "flickr.lua",
            "-o", ItemInterpolation("%(item_dir)s/wget.log"),
            "--no-check-certificate",
            "--output-document", ItemInterpolation("%(item_dir)s/wget.tmp"),
            "--truncate-output",
            "-e", "robots=off",
            "--rotate-dns",
            "--recursive", "--level=inf",
            "--no-parent",
            "--page-requisites",
            "--timeout", "30",
            "--tries", "inf",
            "--domains", "flickr.com",
            "--span-hosts",
            "--waitretry", "30",
            "--warc-file", ItemInterpolation("%(item_dir)s/%(warc_file_base)s"),
            "--warc-header", "operator: Archive Team",
            "--warc-header", "flickr-dld-script-version: " + VERSION,
            "--warc-header", ItemInterpolation("flickr-item: %(item_name)s"),
            "--no-warc-compression",
        ]
        
        item_name = item['item_name']
        item_type, item_value = item_name.split(':', 1)
        
        item['item_type'] = item_type
        item['item_value'] = item_value

        http_client = httpclient.HTTPClient()

        if item_type == 'user':
            wget_args.extend(['--warc-header', 'flickr-user: {}'.format(item_value)])
            wget_args.append('https://www.flickr.com/photos/{}/'.format(item_value))
        if item_type == 'disco':
            raise Exception('Skipping...')
            try:
                r = http_client.fetch('https://www.flickr.com/photos/{}/'.format(item_value), method='GET')
            except httpclient.HTTPError as e:
                r = e.response
            if r.code == 404:
                print('Account was deleted.')
                wget_args.append('https://www.flickr.com/photos/{}/'.format(item_value))
            elif r.code != 200:
                raise Exception('Bad status code, {}.'.format(r.code))
            else:
                text = r.body.decode('utf-8', 'ignore')
                api_key = re.search('root\.YUI_config\.flickr\.api\.site_key\s*=\s*"([^"]+)";', text).group(1)
                req_id = re.search('root\.YUI_config\.flickr\.request\.id\s*=\s*"([^"]+)";', text).group(1)
                item.log_output('Found api_key {} and req_id {}.'.format(api_key, req_id))
                wget_args.append('https://api.flickr.com/services/rest?per_page=50&page=1&extras=can_addmeta%2Ccan_comment%2Ccan_download%2Ccan_share%2Ccontact%2Ccount_comments%2Ccount_faves%2Ccount_views%2Cdate_taken%2Cdate_upload%2Cdescription%2Cicon_urls_deep%2Cisfavorite%2Cispro%2Clicense%2Cmedia%2Cneeds_interstitial%2Cowner_name%2Cowner_datecreate%2Cpath_alias%2Crealname%2Crotation%2Csafety_level%2Csecret_k%2Csecret_h%2Curl_c%2Curl_f%2Curl_h%2Curl_k%2Curl_l%2Curl_m%2Curl_n%2Curl_o%2Curl_q%2Curl_s%2Curl_sq%2Curl_t%2Curl_z%2Cvisibility%2Cvisibility_source%2Co_dims%2Cpubliceditability&get_user_info=1&jump_to=&user_id={}&view_as=use_pref&sort=use_pref&viewerNSID=&method=flickr.people.getPhotos&csrf=&api_key={}&format=json&hermes=1&hermesClient=1&reqId={}&nojsoncallback=1'.format(item_value, api_key, req_id))
        elif item_type == 'photos':
            raise Exception('Skipping...')
            r = http_client.fetch('http://195.201.219.254/' + item_value, method='GET')
            user = item_value.split('/')[0]
            for i in r.body.decode('utf-8', 'ignore').splitlines():
                i = i.strip()
                wget_args.extend(['--warc-header', 'flickr-photo: {}'.format(i)])
                wget_args.extend(['--warc-header', 'flickr-photo-user: {}'.format(user)])
                wget_args.extend(['--warc-header', 'flickr-photo-{}-user: {}'.format(i, user)])
                wget_args.append('https://www.flickr.com/photos/{}/{}/'.format(user, i))
                wget_args.append('https://www.flickr.com/photos/{}/{}/sizes/'.format(user, i))
                wget_args.append('https://www.flickr.com/video_download.gne?id={}'.format(i))
        elif item_type == 'photoscc':
            r = http_client.fetch('http://195.201.219.254/' + item_value, method='GET')
            for s in r.body.decode('utf-8', 'ignore').splitlines():
                s = s.strip(' \t\r\n/')
                if s.startswith('www.flickr.com/photos/'):
                    s = '/'.join(s.split('/')[2:4])
                elif s.startswith('www.flickr.com/'):
                    s = '/'.join(s.split('/')[1:3])
                elif s.startswith('flickr.com/'):
                    s = s.split('/', 1)[1]
                user, i = s.split('/') # NOTE: do not replace with anything that skips invalid urls, we want to catch those with pipeline aborts
                wget_args.extend(['--warc-header', 'flickr-photo: {}'.format(i)])
                wget_args.extend(['--warc-header', 'flickr-photo-user: {}'.format(user)])
                wget_args.extend(['--warc-header', 'flickr-photo-{}-user: {}'.format(i, user)])
                wget_args.append('https://www.flickr.com/photos/{}/{}/'.format(user, i))
                wget_args.append('https://www.flickr.com/video_download.gne?id={}'.format(i))
        else:
            raise Exception('Unknown item')

        http_client.close()
        
        if 'bind_address' in globals():
            wget_args.extend(['--bind-address', globals()['bind_address']])
            print('')
            print('*** Wget will bind address at {0} ***'.format(
                globals()['bind_address']))
            print('')
            
        return realize(wget_args, item)

###########################################################################
# Initialize the project.
#
# This will be shown in the warrior management panel. The logo should not
# be too big. The deadline is optional.
project = Project(
    title="flickr",
    project_html="""
        <img class="project-logo" alt="Project logo" src="http://archiveteam.org/images/thumb/0/03/Flick_logo_black.png/320px-Flick_logo_black.png" height="50px" title=""/>
        <h2>flickr.com <span class="links"><a href="http://flickr.com/">Website</a> &middot; <a href="http://tracker.archiveteam.org/flickr/">Leaderboard</a></span></h2>
        <p>Archiving CC photos from flickr.</p>
    """
)

pipeline = Pipeline(
    CheckIP(),
    GetItemFromTracker("http://%s/%s" % (TRACKER_HOST, TRACKER_ID), downloader,
        VERSION),
    PrepareDirectories(warc_prefix="flickr"),
    WgetDownload(
        WgetArgs(),
        max_tries=2,
        accept_on_exit_code=[0, 4, 8],
        env={
            "item_dir": ItemValue("item_dir"),
            "item_value": ItemValue("item_value"),
            "item_type": ItemValue("item_type"),
            'warc_file_base': ItemValue('warc_file_base')
        }
    ),
    Deduplicate(),
    PrepareStatsForTracker(
        defaults={"downloader": downloader, "version": VERSION},
        file_groups={
            "data": [
                ItemInterpolation("%(item_dir)s/%(warc_file_base)s-deduplicated.warc.gz")
            ]
        },
        id_function=stats_id_function,
    ),
    MoveFiles(),
    LimitConcurrent(NumberConfigValue(min=1, max=20, default="20",
        name="shared:rsync_threads", title="Rsync threads",
        description="The maximum number of concurrent uploads."),
        UploadWithTracker(
            "http://%s/%s" % (TRACKER_HOST, TRACKER_ID),
            downloader=downloader,
            version=VERSION,
            files=[
                ItemInterpolation("%(data_dir)s/%(warc_file_base)s-deduplicated.warc.gz"),
                ItemInterpolation("%(data_dir)s/%(warc_file_base)s_data.txt")
            ],
            rsync_target_source_path=ItemInterpolation("%(data_dir)s/"),
            rsync_extra_args=[
                "--sockopts=SO_SNDBUF=8388608,SO_RCVBUF=8388608", # 02:50 <Kenshin> the extra options should improve rsync speeds when the latency is higher
                "--recursive",
                "--partial",
                "--partial-dir", ".rsync-tmp",
                "--min-size", "1",
                "--no-compress",
                "--compress-level=0"
            ]
            ),
    ),
    SendDoneToTracker(
        tracker_url="http://%s/%s" % (TRACKER_HOST, TRACKER_ID),
        stats=ItemValue("stats")
    )
)


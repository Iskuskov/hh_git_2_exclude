# -*- coding: utf-8 -*-

from __future__ import with_statement

import socket
import subprocess
import nose
import urllib2
import httplib
import time
from functools import partial
import lxml.etree as etree
import contextlib

# XXX взять эти функции из frontik.supervisor, когда он появится
def is_running(port):
    try:
        urllib2.urlopen('http://localhost:%s/status/' % (port,))
        return True
    except urllib2.URLError:
        return False
    except urllib2.HTTPError:
        return False

def stop_worker(port):
    try:
        urllib2.urlopen('http://localhost:%s/stop/' % (port,))
    except urllib2.URLError:
        pass
    except httplib.BadStatusLine:
        pass

def get_page(port, page, xsl=False):
    data = urllib2.urlopen('http://localhost:%s/page/%s/%s' % (port, page, "?noxsl=true" if not xsl else "" ))
    
    return data

def wait_for(fun, n=10):
    for i in range(n):
        if fun():
            return
        time.sleep(0.1)

    assert(fun())

class FrontikTestInstance:
    def __enter__(self):
        for port in xrange(9000, 10000):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(('', port))
                s.close()
                self.port = port
                break
            except:
                pass
        else:
            raise AssertionError('no empty port in 9000-10000 for frontik test instance')

        subprocess.Popen(['python2.6',
                          '../src/frontik_srv.py',
                          '--logfile=./frontik_test.log',
                          '--loglevel=debug',
                          '--host=localhost',
                          '--daemonize=False',
                          '--document_root=./test/',
                          '--port=%s' % (self.port,)])
        wait_for(lambda: is_running(self.port))

        return self.port

    def __exit__(self, exc_type, exc_value, traceback):
        stop_worker(self.port)
        wait_for(lambda: not(is_running(self.port)))

@contextlib.contextmanager
def frontik_get_page_xml(page_name, xsl=True):
    with FrontikTestInstance() as srv_port:
        data = get_page(srv_port, page_name, xsl).read()
        
        try:
            yield etree.fromstring(data)
        except:
            print 'failed to parse xml: "%s"' % (data,)
            raise

@contextlib.contextmanager
def frontik_get_page_text(page_name, xsl=True):
    with FrontikTestInstance() as srv_port:
        data = get_page(srv_port, page_name, xsl).read()
        yield data

def simple_test():
    with frontik_get_page_text('simple') as html:
        assert(not html.find('ok') is None)

def compose_doc_test():
    with frontik_get_page_xml('compose_doc') as xml:
        assert(not xml.find('a') is None)
        assert(xml.findtext('a') == 'aaa')

        assert(not xml.find('b') is None)
        assert(xml.findtext('b') == 'bbb')

        assert(not xml.find('c') is None)
        assert(xml.findtext('c') in [None, ''])

def xsl_transformation_test():
    with frontik_get_page_xml('simple') as html:
        assert (etree.tostring(html) == "<html><body><h1>ok</h1></body></html>")

def test_content_type_with_xsl():
    with FrontikTestInstance() as srv_port:
        assert(get_page(srv_port, 'simple', xsl=True).headers['content-type'].startswith('text/html'))

def test_content_type_wo_xsl():
    with FrontikTestInstance() as srv_port:
        assert(get_page(srv_port, 'simple', xsl=False).headers['content-type'].startswith('application/xml'))

def xml_include_test():
    with frontik_get_page_xml('include_xml') as xml:
        assert(xml.findtext('a') == 'aaa')

if __name__ == '__main__':
    nose.main()

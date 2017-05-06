import os
import sys
import socket
from datetime import datetime
from django.conf import settings
from django.core.management.base import CommandError
# from django.core.management.commands.runserver import Command as RunServerCommand
from django.contrib.staticfiles.management.commands.runserver import Command as RunServerCommand
from django.core.servers.basehttp import  WSGIServer, WSGIRequestHandler
from pserver import __version__
from django.utils import autoreload
from django.utils.six.moves import socketserver

# store the socket at module level so it can be shared between parent a child process
PERSISTENT_SOCK = None
def init_sock(use_ipv6):
    global PERSISTENT_SOCK
    existing_fd = os.environ.get('SERVER_FD')
    if not existing_fd:
        PERSISTENT_SOCK = socket.socket(socket.AF_INET6 if use_ipv6 else socket.AF_INET,
                                        socket.SOCK_STREAM)
        os.environ['SERVER_FD'] = str(PERSISTENT_SOCK.fileno())
    else:
        # print "Reusing existing socket (fd=%s)" % existing_fd
        PERSISTENT_SOCK = socket.fromfd(int(existing_fd),
                                        socket.AF_INET6 if use_ipv6 else socket.AF_INET,
                                        socket.SOCK_STREAM)

def run(addr, port, wsgi_handler, ipv6=False, threading=False, server_cls=WSGIServer):
    global PERSISTENT_SOCK
    init_sock(ipv6)
    server_address = (addr, port)
    if threading:
        httpd_cls = type('WSGIServer', (socketserver.ThreadingMixIn, server_cls), {})
    else:
        httpd_cls = server_cls
    httpd = httpd_cls(server_address, WSGIRequestHandler, ipv6=ipv6)
    httpd.socket = PERSISTENT_SOCK
    if threading:
        # ThreadingMixIn.daemon_threads indicates how threads will behave on an
        # abrupt shutdown; like quitting the server by the user or restarting
        # by the auto-reloader. True means the server will not wait for thread
        # termination before it quits. This will make auto-reloader faster
        # and will prevent the need to kill the server manually if a thread
        # isn't terminating correctly.
        httpd.daemon_threads = True
    try:
        httpd.server_bind()
    except  Exception as e:
        if 'Errno 22' in str(e):
            # may have been bound, just emulate some stuff done in server_bind (like setting up environ)
            httpd.server_name = socket.getfqdn(addr)
            httpd.server_port = port
            httpd.setup_environ()
        else:
            raise e
    httpd.server_activate()
    httpd.set_app(wsgi_handler)
    httpd.serve_forever()


class Command(RunServerCommand):
    # option_list = RunServerCommand.option_list
    help = "Starts a persistent web server that reuses its listening socket on reload."

    def inner_run(self, *args, **options):
        # If an exception was silenced in ManagementUtility.execute in order
        # to be raised in the child process, raise it now.
        autoreload.raise_last_exception()

        threading = options['use_threading']
        # 'shutdown_message' is a stealth option.
        shutdown_message = options.get('shutdown_message', '')
        quit_command = 'CTRL-BREAK' if sys.platform == 'win32' else 'CONTROL-C'

        self.stdout.write("Performing system checks...\n\n")
        self.check(display_num_errors=True)
        # Need to check migrations here, so can't use the
        # requires_migrations_check attribute.
        self.check_migrations()
        now = datetime.now().strftime('%B %d, %Y - %X')
        self.stdout.write(now)
        self.stdout.write((
            "Django version %(version)s, using settings %(settings)r\n"
            "Starting development server at %(protocol)s://%(addr)s:%(port)s/\n"
            "Quit the server with %(quit_command)s.\n"
        ) % {
            "version": self.get_version(),
            "settings": settings.SETTINGS_MODULE,
            "protocol": self.protocol,
            "addr": '[%s]' % self.addr if self._raw_ipv6 else self.addr,
            "port": self.port,
            "quit_command": quit_command,
        })

        try:
            handler = self.get_handler(*args, **options)
            run(self.addr, int(self.port), handler,
                ipv6=self.use_ipv6, threading=threading, server_cls=self.server_cls)
        except socket.error as e:
            # Use helpful error messages instead of ugly tracebacks.
            ERRORS = {
                errno.EACCES: "You don't have permission to access that port.",
                errno.EADDRINUSE: "That port is already in use.",
                errno.EADDRNOTAVAIL: "That IP address can't be assigned to.",
            }
            try:
                error_text = ERRORS[e.errno]
            except KeyError:
                error_text = e
            self.stderr.write("Error: %s" % error_text)
            # Need to use an OS exit because sys.exit doesn't work in a thread
            os._exit(1)
        except KeyboardInterrupt:
            if shutdown_message:
                self.stdout.write(shutdown_message)
            sys.exit(0)


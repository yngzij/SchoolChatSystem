import asyncio
import os.path
import psycopg2
import re
import tornado.escape
import tornado.httpserver
import tornado.ioloop
import tornado.locks
import tornado.options
import tornado.web

import uuid


from tornado.options import define, options

define("port", default=8888, help="run on the given port", type=int)
define("db_host", default="127.0.0.1", help="blog database host")
define("db_port", default=5432, help="blog database port")
define("db_database", default="postgres", help="blog database name")
define("db_user", default="postgres", help="blog database user")
define("db_password", default="ta", help="blog database password")



class MessageBuffer(object):
    def __init__(self):
        # cond is notified whenever the message cache is updated
        self.cond = tornado.locks.Condition()
        self.cache = []
        self.cache_size = 200

    def get_messages_since(self, cursor):
        """Returns a list of messages newer than the given cursor.
        ``cursor`` should be the ``id`` of the last message received.
        """
        results = []
        for msg in reversed(self.cache):
            if msg["id"] == cursor:
                break
            results.append(msg)
        results.reverse()
        return results

    def add_message(self, message):
        self.cache.append(message)
        if len(self.cache) > self.cache_size:
            self.cache = self.cache[-self.cache_size :]
        self.cond.notify_all()


# Making this a non-singleton is left as an exercise for the reader.
global_message_buffer = MessageBuffer()
access_number=0



class MessageNewHandler(tornado.web.RequestHandler):
    def post(self):
        name_msg=self.get_secure_cookie("username")[-5:]
        name_msg=bytes.decode(name_msg).__str__()
        msg=name_msg+" :  "+self.get_argument('body')

        message = {"id": str(uuid.uuid4()), "body": msg}
        message["html"] = tornado.escape.to_unicode(
            self.render_string("message.html", message=message)
        )

        if self.get_argument("next", None):
            self.redirect(self.get_argument("next"))
        else:
            self.write(message)
        global_message_buffer.add_message(message)


class MessageUpdatesHandler(tornado.web.RequestHandler):
    async def post(self):
        cursor = self.get_argument("cursor", None)
        messages = global_message_buffer.get_messages_since(cursor)
        while not messages:
            # Save the Future returned here so we can cancel it in
            # on_connection_close.
            self.wait_future = global_message_buffer.cond.wait()
            try:
                await self.wait_future
            except asyncio.CancelledError:
                return
            messages = global_message_buffer.get_messages_since(cursor)
        if self.request.connection.stream.closed():
            return
        self.write(dict(messages=messages))

    def on_connection_close(self):
        self.wait_future.cancel()


async def maybe_create_tables(db):
    try:
        with (await db.cursor()) as cur:
            await cur.execute("SELECT COUNT(*) FROM entries LIMIT 1")
            await cur.fetchone()
    except psycopg2.ProgrammingError:
        with open("schema.sql") as f:
            schema = f.read()
        with (await db.cursor()) as cur:
            await cur.execute(schema)

class NoResultError(Exception):
    pass


class BaseHandler(tornado.web.RequestHandler):
    def row_to_obj(self, row, cur):
        """Convert a SQL row to an object supporting dict and attribute access."""
        obj = tornado.util.ObjectDict()
        for val, desc in zip(row, cur.description):
            obj[desc.name] = val
        return obj

    async def execute(self, stmt, *args):
        with (await self.application.db.cursor()) as cur:
            await cur.execute(stmt, args)

    async def query(self, stmt, *args):
        with (await self.application.db.cursor()) as cur:
            await cur.execute(stmt, args)
            return [self.row_to_obj(row, cur) for row in await cur.fetchall()]

    async def queryone(self, stmt, *args):
        results = await self.query(stmt, *args)
        if len(results) == 0:
            raise NoResultError()
        elif len(results) > 1:
            raise ValueError("Expected 1 result, got %d" % len(results))
        return results[0]

    async def prepare(self):
        user_id = self.get_secure_cookie("blogdemo_user")
        if user_id:
            self.current_user = await self.queryone(
                "SELECT * FROM authors WHERE id = %s", int(user_id)
            )

async def any_author_exists(self):
    return bool(await self.query("SELECT * FROM authors LIMIT 1"))


class IndexHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_secure_cookie("username",str(uuid.uuid4()))
        global access_number
        access_number+=1
        print(access_number)
        self.render("index.html")


class ChatHandler(tornado.web.RequestHandler):
    def get(self):
        self.render("chat.html", messages=global_message_buffer.cache,access_number=access_number)


class Application(tornado.web.Application):
    def __init__(self, db=None):
        self.db=None
        if not db:
            self.db=db
        handlers = [
            (r"/", IndexHandler),
            (r"/chat",ChatHandler),
            (r"/a/message/new",MessageNewHandler),
            (r"/a/message/updates",MessageUpdatesHandler),
        ]
        settings = dict(
            blog_title=u"大家一起Happy!!!!",
            template_path=os.path.join(os.path.dirname(__file__), "templates"),
            static_path=os.path.join(os.path.dirname(__file__), "static"),
            #ui_modules={"Entry": EntryModule},
            xsrf_cookies=True,
            cookie_secret="__TODO:_GENERATE_YOUR_OWN_RANDOM_VALUE_HERE__AAA",
            login_url="/auth/login",
            debug=True,
            access_number=0
        )
        super(Application,self).__init__(handlers,**settings)

async def main():
    tornado.options.parse_command_line()
    '''
    async with aiopg.create_pool(
            host=options.db_host,
            port=options.db_port,
            user=options.db_user,
            password=options.db_password,
            dbname=options.db_database,
    ) as db:
    await maybe_create_tables(db=db)
    '''
    app = Application()
    app.listen(options.port)
    shutdown_event = tornado.locks.Event()
    await shutdown_event.wait()

if __name__ == '__main__':
    tornado.ioloop.IOLoop.current().run_sync(main)



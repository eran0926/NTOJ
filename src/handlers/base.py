import re
import asyncio
import datetime
import json

import asyncpg
import tornado.gen
import tornado.template
import tornado.web
import tornado.websocket
from redis import asyncio as aioredis

from services.contests import ContestService, Contest
from services.user import UserService, Account
import utils.htmlgen

TEMPLATE_NAMESPACE = {
    'set_page_title': utils.htmlgen.set_page_title,
    'markdown_escape': utils.htmlgen.markdown_escape,
}

class RequestHandler(tornado.web.RequestHandler):
    def __init__(self, *args, **kwargs):
        self.db: asyncpg.Pool = kwargs.pop('db')
        self.rs: aioredis.Redis = kwargs.pop('rs')
        self.tpldr = tornado.template.Loader('static/templ', namespace=TEMPLATE_NAMESPACE)

        self.acct: Account = None
        self.contest: Contest = None

        super().__init__(*args, **kwargs)

        try:
            self.get_argument('json')
            self.res_json = True

        except tornado.web.HTTPError:
            self.res_json = False

    def error(self, err):
        self.finish(err)
        return

    async def render(self, templ, **kwargs):

        class _encoder(json.JSONEncoder):
            def default(self, o):
                if isinstance(o, datetime.datetime):
                    return o.isoformat()

                else:
                    return json.JSONEncoder.default(self, o)

        kwargs['user'] = self.acct

        if self.res_json is True:
            self.finish(json.dumps(kwargs, cls=_encoder))

        else:
            data = self.tpldr.load(templ + '.html').generate(**kwargs)
            self.finish(data)


class WebSocketHandler(tornado.websocket.WebSocketHandler):
    def __init__(self, *args, **kwargs):
        self.db: asyncpg.Pool = kwargs.pop('db')
        self.rs: aioredis.Redis = kwargs.pop('rs')

        super().__init__(*args, **kwargs)


class WebSocketSubHandler(tornado.websocket.WebSocketHandler):
    def __init__(self, *args, **kwargs):
        pool = kwargs.pop('pool')
        self.rs: aioredis.Redis = aioredis.Redis(connection_pool=pool)
        self.p = self.rs.pubsub()
        self.task: asyncio.Task = None

        super().__init__(*args, **kwargs)
        self.settings['websocket_ping_interval'] = 10

    def check_origin(self, _: str) -> bool:
        return True

    def on_close(self) -> None:
        self.task.cancel()
        asyncio.create_task(self.rs.aclose())


def reqenv(func):
    async def wrap(self, *args, **kwargs):
        path = str(self.request.path)
        if (g := re.search(r'contests/(\d+)/?', path)) is not None:
            contest_id = g.group(1)
            if not contest_id.isnumeric():
                await self.finish('Enoext')
                return

            _, self.contest = await ContestService.inst.get_contest(int(contest_id))
            if self.contest is None:
                await self.finish('Enoext')
                return

        _, acct_id, _ = await UserService.inst.info_sign(self)
        _, self.acct = await UserService.inst.info_acct(acct_id)

        ret = await func(self, *args, **kwargs)
        return ret

    return wrap

GOTO_SIGN="""
<script type="text/javascript" id="contjs">
function init() {
    index.go('/oj/sign/');
}
</script>
"""

def require_permission(acct_type):
    def decorator(func):
        async def wrap(self, *args, **kwargs):
            if isinstance(acct_type, list):
                if self.acct.acct_type not in acct_type:
                    if self.acct.is_guest():
                        self.finish(GOTO_SIGN)
                        return

                    await self.finish('Eacces')
                    return

            elif self.acct.acct_type != acct_type:
                if self.acct.is_guest():
                    self.finish(GOTO_SIGN)
                    return

                await self.finish('Eacces')
                return

            ret = await func(self, *args, **kwargs)
            return ret

        return wrap

    return decorator

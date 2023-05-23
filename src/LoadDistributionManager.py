import base64
import io
import time
import traceback
import json
from enum import Enum
from collections import deque
from threading import Thread

import discord

from src import AutoWebUi


def _worker_loop(queue, ip, config, webui):
    if not queue:
        return

    default_height = config.config['command_params']['default_height']
    default_width = config.config['command_params']['default_width']
    default_cfg = config.config['command_params']['default_cfg']
    wrap_spoiler = config.config.get(
        'command_params',
        {},
    ).get('wrap_spoiler', True)

    try:
        start_time = time.time()
        queue_obj = queue.popleft()

        response, status_code = webui.txt_to_img(queue_obj)
        if status_code != 200:
            embed = discord.Embed(
                title='Encountered an error: ',
                description=f'Status code: {status_code}\n{response}',
                color=0xff0000,
            )
            queue_obj.event_loop.create_task(
                queue_obj.ctx.channel.send(embed=embed),
            )

            return

        embed = discord.Embed()
        params = response['parameters']

        prompt = params['prompt']
        nprompt = params['negative_prompt']

        embed.add_field(
            name='Prompt',
            value=(prompt[:1020] + '...') if len(prompt) > 1020 else prompt,
        )
        embed.add_field(
            name='Negative Prompt',
            value=(nprompt[:1020] + '...') if len(nprompt) > 1020 else nprompt,
        )
        embed.add_field(
            name='Steps',
            value=params['steps'],
        )

        if (
            params['height'] != default_height or
            params['width'] != default_width
        ):
            embed.add_field(name='Height', value=params['height'])
            embed.add_field(name='Width', value=params['width'])

        embed.add_field(
            name='Sampler',
            value=params['sampler_index'],
        )
        embed.add_field(
            name='Seed',
            value=json.loads(response['info'])['seed'],
        )

        if params['cfg_scale'] != default_cfg:
            embed.add_field(name='CFG Scale', value=params['cfg_scale'])

        if params['enable_hr']:
            embed.add_field(name='Highres Fix', value='True')

        compute_time = time.time() - start_time
        footer_text = (
            f'{queue_obj.ctx.author.name}#'
            f'{queue_obj.ctx.author.discriminator}'
            f'   |   compute used: {compute_time:.2f} seconds'
            f'   |   react with ❌ to delete'
        )

        if queue_obj.ctx.author.avatar is None:
            embed.set_footer(text=footer_text)
        else:
            embed.set_footer(
                text=footer_text,
                icon_url=queue_obj.ctx.author.avatar.url
            )

        image = None
        for i in response['images']:
            image = io.BytesIO(base64.b64decode(i.split(",", 1)[0]))

        queue_obj.event_loop.create_task(
            queue_obj.ctx.channel.send(
                file=discord.File(
                    fp=image,
                    filename='image.png',
                    spoiler=wrap_spoiler
                ),
                embed=embed
            ),
        )
    except Exception:
        tb = traceback.format_exc()
        # check if the queue object was retrieved before the error
        if 'queue_obj' in locals():
            embed = discord.Embed(title='Encountered an error: ',
                                  description=str(tb),
                                  color=0xff0000)
            # send the error to the user who requested the command that
            # errored
            queue_obj.event_loop.create_task(
                queue_obj.ctx.channel.send(embed=embed),
            )
        else:
            # otherwise print to console
            print(tb)


def _worker(queue, ip, config):
    webui = AutoWebUi.WebUi(ip)
    if webui.heartbeat():
        print('connected to webui at', ip)
        while True:
            _worker_loop(queue, ip, config, webui)
            time.sleep(1)
    else:
        print('Connection to webui', ip, 'failed')


class Status(Enum):
    QUEUED = 0
    IN_QUEUE = 2


class LoadDist:
    def __init__(self, ips, config):
        self.instances = []
        self.queue = deque()
        self.config = config
        for ip in ips:
            self.instances.append(
                Thread(target=_worker, args=(self.queue, ip, self.config)),
            )
        for instance in self.instances:
            instance.start()

        self.max_per_user = self.config.config.get(
            'command_params',
            {},
        ).get('max_per_user', 5)

    def add_to_queue(self, queue_obj):
        author_id = queue_obj.ctx.author.id

        # Count the number of objets in the queue with the same author id
        num_in_queue = 0
        queue_pos = 0
        for obj in self.queue:
            queue_pos += 1
            if obj.ctx.author.id == author_id:
                num_in_queue += 1

        if num_in_queue >= self.max_per_user:
            return Status.IN_QUEUE, queue_pos

        self.queue.append(queue_obj)
        return Status.QUEUED, len(self.queue) - 1

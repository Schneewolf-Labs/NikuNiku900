import base64
import contextlib
import discord
import io
import random
import requests
import time
import traceback
from asyncio import AbstractEventLoop
from PIL import Image, PngImagePlugin, ImageDraw, ImageFont
from discord import option
from discord.ext import commands
from discord.ui import View
from threading import Thread
from typing import Optional

from core import queuehandler
from core import viewhandler
from core import settings
from core import settingscog


class NikuCog(commands.Cog, name='NikuNiku900', description='Generate anime images from your prompt!'):
    ctx_parse = discord.ApplicationContext

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(viewhandler.DrawView(self))

    @commands.slash_command(name='invite', description='Grant the VIP invite role to a user', guild_only=True, guild_ids=settings.global_var.privileged_guilds)
    @option(
        'user',
        discord.User,
        description="User to invite",
        required=True
    )
    async def invite_handler(self, ctx: discord.ApplicationContext, *, user: discord.User):
        try:
            # get role id from globalvar
            role_id = settings.global_var.vip_invite_role
            # get role from id
            role = ctx.guild.get_role(role_id)
            # add role to user
            await user.add_roles(role)
            # send confirmation
            await ctx.send_response(f'Added role ``{role.name}`` to ``{user.name}#{user.discriminator}``')
        except Exception as e:
            print(e)
            await ctx.send_response('Something went wrong!')

    @commands.slash_command(name='generate', description='Create an image', guild_only=True)
    @option(
        'prompt',
        str,
        description='A prompt to condition the model with.',
        required=True,
    )
    @option(
        'negative_prompt',
        str,
        description='Negative prompts to exclude from output.',
        required=False,
    )
    @option(
        'seed',
        int,
        description='The seed to use for reproducibility.',
        required=False,
    )
    @option(
        'spoiler',
        bool,
        description='Hide the image in a spoiler.',
        required=False,
    )
    async def dream_handler(self, ctx: discord.ApplicationContext, *,
                            prompt: str, negative_prompt: str = None,
                            seed: int = None, spoiler: bool = None):

        # update defaults with any new defaults from settingscog
        channel = '% s' % ctx.channel.id
        settings.check(channel)
        if negative_prompt is None:
            negative_prompt = settings.read(channel)['negative_prompt']
        if spoiler is None:
            spoiler = settings.read(channel)['spoiler']

        steps = settings.read(channel)['steps']
        width = settings.read(channel)['width']
        if width < 768:
            width = 768
        height = settings.read(channel)['height']
        if height < 768:
            height = 768
        guidance_scale = settings.read(channel)['guidance_scale']
        sampler = settings.read(channel)['sampler']
        style = settings.read(channel)['style']
        facefix = settings.read(channel)['facefix']
        highres_fix = settings.read(channel)['highres_fix']
        clip_skip = settings.read(channel)['clip_skip']
        hypernet = settings.read(channel)['hypernet']
        lora = settings.read(channel)['lora']
        strength = settings.read(channel)['strength']
        count = settings.read(channel)['count']

        init_image = None
        init_url = None

        # if a model is not selected, do nothing
        model_name = 'Default'
        data_model = settings.read(channel)['data_model']

        simple_prompt = prompt
        # take selected data_model and get model_name, then update data_model with the full name
        for model in settings.global_var.model_info.items():
            if model[0] == data_model:
                model_name = model[0]
                data_model = model[1][0]
                # look at the model for activator token and prepend prompt with it
                if model[1][3]:
                    prompt = model[1][3] + " " + prompt
                break

        # if a hypernet or lora is used, append it to the prompt
        if hypernet != 'None':
            prompt += f' <hypernet:{hypernet}:0.85>'
        if lora != 'None':
            prompt += f' <lora:{lora}:0.85>'

        if data_model != '':
            print(f'Request -- {ctx.author.name}#{ctx.author.discriminator} -- Prompt: {prompt}')
        else:
            print(f'Request -- {ctx.author.name}#{ctx.author.discriminator} -- Prompt: {prompt} -- Using model: {data_model}')

        if seed == -1:
            seed = random.randint(0, 0xFFFFFFFF)

        # url *will* override init image for compatibility, can be changed here
        if init_url:
            try:
                init_image = requests.get(init_url)
            except(Exception,):
                await ctx.send_response('URL image not found!\nI will do my best without it!')

        # formatting aiya initial reply
        reply_adds = ''
        # lower step value to the highest setting if user goes over max steps
        if steps > settings.read(channel)['max_steps']:
            steps = settings.read(channel)['max_steps']
            reply_adds += f'\nExceeded maximum of ``{steps}`` steps! This is the best I can do...'
        # if model_name != 'Default':
        #     reply_adds += f'\nModel: ``{model_name}``'
        # if negative_prompt != '':
        #     reply_adds += f'\nNegative Prompt: ``{negative_prompt}``'
        # if (width != 512) or (height != 512):
        #     reply_adds += f'\nSize: ``{width}``x``{height}``'
        if guidance_scale != '7.0':
            try:
                float(guidance_scale)
                reply_adds += f'\nGuidance Scale: ``{guidance_scale}``'
            except(Exception,):
                reply_adds += f"\nGuidance Scale can't be ``{guidance_scale}``! Setting to default of `7.0`."
                guidance_scale = 7.0
        if sampler != 'Euler a':
            reply_adds += f'\nSampler: ``{sampler}``'
        if init_image:
            reply_adds += f'\nStrength: ``{strength}``'
            reply_adds += f'\nURL Init Image: ``{init_image.url}``'
        if count != 1:
            max_count = settings.read(channel)['max_count']
            if count > max_count:
                count = max_count
                reply_adds += f'\nExceeded maximum of ``{count}`` images! This is the best I can do...'
            reply_adds += f'\nCount: ``{count}``'
        if style != 'None':
            reply_adds += f'\nStyle: ``{style}``'
        if hypernet != 'None':
            reply_adds += f'\nHypernet: ``{hypernet}``'
        if lora != 'None':
            reply_adds += f'\nLoRA: ``{lora}``'
        if facefix != 'None':
            reply_adds += f'\nFace restoration: ``{facefix}``'
        if clip_skip != 1:
            reply_adds += f'\nCLIP skip: ``{clip_skip}``'

        # set up tuple of parameters to pass into the Discord view
        input_tuple = (
            ctx, simple_prompt, prompt, negative_prompt, data_model, steps, width, height, guidance_scale, sampler, seed, strength,
            init_image, count, style, facefix, highres_fix, clip_skip, hypernet, lora, spoiler)
        view = View()
        # setup the queue
        if queuehandler.GlobalQueue.dream_thread.is_alive():
            user_already_in_queue = False
            for queue_object in queuehandler.GlobalQueue.queue:
                if queue_object.ctx.author.id == ctx.author.id:
                    user_already_in_queue = True
                    break
            if user_already_in_queue:
                await ctx.send_response(content=f'Please wait! You\'re queued up.', ephemeral=True)
            else:
                queuehandler.GlobalQueue.queue.append(queuehandler.DrawObject(self, *input_tuple, view))
                await ctx.send_response(
                    f'<@{ctx.author.id}>, {settings.messages()}\nQueue: ``{len(queuehandler.GlobalQueue.queue)}`` - ``{simple_prompt}`` - Seed: ``{seed}``{reply_adds}')
        else:
            await queuehandler.process_dream(self, queuehandler.DrawObject(self, *input_tuple, view))
            await ctx.send_response(
                f'<@{ctx.author.id}>, {settings.messages()}\nQueue: ``{len(queuehandler.GlobalQueue.queue)}`` - ``{simple_prompt}`` - Seed: ``{seed}``{reply_adds}')

    # the function to queue Discord posts
    def post(self, event_loop: AbstractEventLoop, post_queue_object: queuehandler.PostObject):
        event_loop.create_task(
            post_queue_object.ctx.channel.send(
                content=post_queue_object.content,
                files=post_queue_object.files,
                view=post_queue_object.view
            )
        )
        if queuehandler.GlobalQueue.post_queue:
            self.post(self.event_loop, self.queue.pop(0))

    # generate the image
    def dream(self, event_loop: AbstractEventLoop, queue_object: queuehandler.DrawObject):
        try:
            start_time = time.time()

            # create persistent session since we'll need to do a few API calls
            s = requests.Session()
            if settings.global_var.api_auth:
                s.auth = (settings.global_var.api_user, settings.global_var.api_pass)

            # construct a payload for data model, then the normal payload
            model_payload = {
                "sd_model_checkpoint": queue_object.data_model
            }
            payload = {
                "prompt": queue_object.prompt,
                "negative_prompt": queue_object.negative_prompt,
                "steps": queue_object.steps,
                "width": queue_object.width,
                "height": queue_object.height,
                "cfg_scale": queue_object.guidance_scale,
                "sampler_index": queue_object.sampler,
                "seed": queue_object.seed,
                "seed_resize_from_h": 0,
                "seed_resize_from_w": 0,
                "denoising_strength": None,
                "n_iter": queue_object.batch_count,
                "styles": [
                    queue_object.style
                ]
            }

            # update payload if init_img or init_url is used
            if queue_object.init_image is not None:
                image = base64.b64encode(requests.get(queue_object.init_image.url, stream=True).content).decode('utf-8')
                img_payload = {
                    "init_images": [
                        'data:image/png;base64,' + image
                    ],
                    "denoising_strength": queue_object.strength
                }
                payload.update(img_payload)

            # update payload if high-res fix is used
            if queue_object.highres_fix != 'Disabled':
                highres_payload = {
                    "enable_hr": True,
                    "hr_upscaler": queue_object.highres_fix,
                    "hr_scale": 1,
                    "hr_second_pass_steps": int(queue_object.steps)/2,
                    "denoising_strength": queue_object.strength
                }
                payload.update(highres_payload)

            # add any options that would go into the override_settings
            override_settings = {"CLIP_stop_at_last_layers": queue_object.clip_skip}
            if queue_object.facefix != 'None':
                override_settings["face_restoration_model"] = queue_object.facefix
                # face restoration needs this extra parameter
                facefix_payload = {
                    "restore_faces": True,
                }
                payload.update(facefix_payload)

            # update payload with override_settings
            override_payload = {
                "override_settings": override_settings
            }
            payload.update(override_payload)

            # send normal payload to webui
            if settings.global_var.gradio_auth:
                login_payload = {
                    'username': settings.global_var.username,
                    'password': settings.global_var.password
                }
                s.post(settings.global_var.url + '/login', data=login_payload)
            else:
                s.post(settings.global_var.url + '/login')

            # only send model payload if one is defined
            if queue_object.data_model != '':
                s.post(url=f'{settings.global_var.url}/sdapi/v1/options', json=model_payload)
            if queue_object.init_image is not None:
                response = s.post(url=f'{settings.global_var.url}/sdapi/v1/img2img', json=payload)
            else:
                response = s.post(url=f'{settings.global_var.url}/sdapi/v1/txt2img', json=payload)
            response_data = response.json()
            end_time = time.time()

            # create safe/sanitized filename
            keep_chars = (' ', '.', '_')
            file_name = "".join(c for c in queue_object.simple_prompt if c.isalnum() or c in keep_chars).rstrip()

            # save local copy of image and prepare PIL images
            pil_images = []
            for i, image_base64 in enumerate(response_data['images']):
                image = Image.open(io.BytesIO(base64.b64decode(image_base64.split(",", 1)[0])))
                # watermark the image
                draw = ImageDraw.Draw(image)
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
                watermark_text = "NikuNiku900.com"
                text_size = draw.textsize(watermark_text, font)
                draw.text((image.width-text_size[0]-8, image.height-text_size[1]-8), watermark_text, fill=(255, 255, 255), font=font)
                draw.text((image.width-text_size[0]-10, image.height-text_size[1]-10), watermark_text, fill=(171, 107, 205), font=font)
                # add to list of PIL images
                pil_images.append(image)

                # grab png info
                png_payload = {
                    "image": "data:image/png;base64," + image_base64
                }
                png_response = s.post(url=f'{settings.global_var.url}/sdapi/v1/png-info', json=png_payload)

                metadata = PngImagePlugin.PngInfo()
                epoch_time = int(time.time())
                metadata.add_text("parameters", png_response.json().get("info"))
                file_path = f'{settings.global_var.dir}/{epoch_time}-{queue_object.seed}-{file_name[0:120]}-{i}.png'
                image.save(file_path, pnginfo=metadata)
                print(f'Saved image: {file_path}')

            # increment number of images generated
            settings.stats_count(queue_object.batch_count)

            # post to discord
            def post_dream():
                with contextlib.ExitStack() as stack:
                    buffer_handles = [stack.enter_context(io.BytesIO()) for _ in pil_images]

                    image_count = len(pil_images)
                    noun_descriptor = "drawing" if image_count == 1 else f'{image_count} drawings'

                    for (pil_image, buffer) in zip(pil_images, buffer_handles):
                        pil_image.save(buffer, 'PNG', pnginfo=metadata)
                        buffer.seek(0)
                    draw_time = '{0:.3f}'.format(end_time - start_time)
                    message = f'my {noun_descriptor} of ``{queue_object.simple_prompt}`` took me ``{draw_time}`` ' \
                              f'seconds!\n> *{queue_object.ctx.author.name}#{queue_object.ctx.author.discriminator}*'
                    files = [discord.File(fp=buffer, filename=f'{queue_object.seed}-{i}.png', spoiler=queue_object.spoiler) for (i, buffer) in
                             enumerate(buffer_handles)]

                    queuehandler.process_post(
                        self, queuehandler.PostObject(
                            self, queue_object.ctx, content=f'<@{queue_object.ctx.author.id}>, {message}', file='', files=files, embed='', view=queue_object.view))
            Thread(target=post_dream, daemon=True).start()

        except KeyError:
            embed = discord.Embed(title='txt2img failed', description=f'An invalid parameter was found!',
                                  color=settings.global_var.embed_color)
            event_loop.create_task(queue_object.ctx.channel.send(embed=embed))
        except Exception as e:
            embed = discord.Embed(title='txt2img failed', description=f'{e}\n{traceback.print_exc()}',
                                  color=settings.global_var.embed_color)
            event_loop.create_task(queue_object.ctx.channel.send(embed=embed))
        # check each queue for any remaining tasks
        queuehandler.process_queue()


def setup(bot):
    bot.add_cog(NikuCog(bot))

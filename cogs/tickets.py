import io
from typing import Literal, List, Dict

import chat_exporter
import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands

from discord_bot_owners import DiscordBotOwners


async def create_ticket(interaction: discord.Interaction, category: str, stars: str = None) -> None:
    guild_data = await interaction.client.mongo.fetch_guild_data()
    if category in guild_data["tickets"]:
        current_ticket_id = guild_data["tickets"][category].get(str(interaction.user.id))
        if current_ticket_id is not None:
            return await interaction.response.send_message(
                f"You already have a ticket opened in this category, <#{current_ticket_id}>.", ephemeral=True
            )

    if stars is not None and stars not in {"1", "2", "3"}:
        return await interaction.response.send_message(
            "The number of requested stars must be either 1, 2 or 3.", ephemeral=True
        )

    await interaction.response.send_message("Your ticket is being created...", ephemeral=True)

    tickets_category = interaction.guild.get_channel(interaction.client.config["category_id"]["tickets"])

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True)
    }
    ticket_name = f"-{interaction.user.name}-{interaction.user.discriminator}"

    if stars is not None:
        category_manager_role = interaction.guild.get_role(
            interaction.client.config["role_id"][f"{category.lower()}_developer"]["manager"]
        )
        overwrites[category_manager_role] = discord.PermissionOverwrite(read_messages=True, manage_messages=True)
        ticket_name = f"{interaction.client.config['tickets'][category][2]}" + ticket_name
    else:
        ticket_name = f"support" + ticket_name

    ticket_channel = await interaction.guild.create_text_channel(
        ticket_name, overwrites=overwrites, category=tickets_category
    )

    await interaction.client.mongo.update_guild_data_document(
        {"$set": {f"tickets.{category}.{interaction.user.id}": ticket_channel.id}}
    )

    ticket_embed = discord.Embed(
        title=f"Ticket",
        description=f"Welcome {interaction.user.mention} to Discord Bot Owner's ticket system.\n\n"
                    f"Please wait for {'a manager' if stars is not None else 'an administrator'} to handle your "
                    f"ticket.",
        color=interaction.client.color,
        timestamp=discord.utils.utcnow()
    )

    if stars is not None:
        ticket_embed.add_field(name="Category", value=category, inline=False)
        ticket_embed.add_field(name="Stars", value=stars, inline=False)

    ticket_embed.set_footer(text="Discord Bot Owners", icon_url=interaction.client.user.display_avatar)

    await ticket_channel.send(embed=ticket_embed)
    fake_ping = await ticket_channel.send(f"{interaction.user.mention}")
    await fake_ping.delete()

    await interaction.edit_original_response(content=f"Your ticket has been created, {ticket_channel.mention}.")


""" Tickets view. """


class TicketCreationModal(discord.ui.Modal, title="Create a Ticket"):

    def __init__(self, category: str):
        super().__init__()
        self.category = category

    stars_requested = discord.ui.TextInput(
        label="Stars requested",
        style=discord.TextStyle.short,
        placeholder="Type either 1, 2 or 3",
        max_length=1
    )

    async def on_submit(self, interaction: discord.Interaction):
        await create_ticket(interaction, self.category, self.stars_requested.value)


class TicketsDropdown(discord.ui.Select):

    def __init__(self, config: Dict):
        options = [
            discord.SelectOption(label=key, description=value[0], emoji=value[1]) for key, value in
            config["tickets"].items()
        ]
        super().__init__(placeholder="Select a category...", options=options)

    async def callback(self, interaction: discord.Interaction):
        return await interaction.response.send_modal(TicketCreationModal(self.values[0]))


class TicketsView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Skill Evaluation", emoji="⭐", style=discord.ButtonStyle.blurple, custom_id="persisten:skill_eval"
    )
    async def skill_evaluation(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = discord.ui.View()
        view.add_item(TicketsDropdown(interaction.client.config))
        await interaction.response.send_message(view=view, ephemeral=True)

    @discord.ui.button(
        label="Support", emoji="❓", style=discord.ButtonStyle.blurple, custom_id="persisten:support"
    )
    async def support(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await create_ticket(interaction, "Support")


class Tickets(commands.Cog):
    """The cog to manage tickets."""

    def __init__(self, client: DiscordBotOwners):
        self.client = client

    async def cog_load(self) -> None:
        self.client.loop.create_task(self.after_ready())

    async def after_ready(self) -> None:
        await self.client.wait_until_ready()

        guild_data = await self.client.mongo.fetch_guild_data()
        if guild_data["tickets_message_id"] is None:
            return

        self.client.add_view(TicketsView(), message_id=guild_data["tickets_message_id"])

    async def send_tickets_view(self, channel, **kwargs) -> None:
        tickets_embed = discord.Embed(
            title="Create a ticket",
            description="Select the category you are willing to create a ticket about using the buttons below.",
            color=self.client.color
        )

        msg = await channel.send(embed=tickets_embed, view=TicketsView(), **kwargs)
        await self.client.mongo.update_guild_data_document(
            {"$set": {"tickets_message_id": msg.id, "tickets_channel_id": channel.id}}
        )
        await self.client.reload_extension("cogs.tickets")

    """ Tickets commands. """

    @app_commands.command(name="close")
    @app_commands.default_permissions()
    async def close(self, interaction: discord.Interaction):
        """Close a ticket."""
        if interaction.channel.category_id != self.client.config["category_id"]["tickets"]:
            return await interaction.response.send_message("This channel is not a ticket.", ephemeral=True)

        if interaction.user.get_role(self.client.config["role_id"]["manager"]) is None or \
                interaction.user.guild_permissions.administrator is False:
            return await interaction.response.send_message(content="You can't do that.", ephemeral=True)

        await interaction.response.send_message("This ticket will soon be closed.")

        overwrites = {interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False)}
        await interaction.channel.edit(overwrites=overwrites)

        guild_data = await self.client.mongo.fetch_guild_data()

        user_id = None
        category = None
        for ticket_category, tickets in guild_data["tickets"].items():
            for usr_id, channel_id in tickets.items():
                if channel_id == interaction.channel.id:
                    user_id = int(usr_id)
                    category = ticket_category
                    break

            if user_id is not None:
                break

        if user_id is None:
            # Why are we here. Shouldn't be possible.
            return

        try:
            await self.client.mongo.update_guild_data_document({"$unset": {f"tickets.{category}.{user_id}": ""}})
        except KeyError:
            # It's a race condition if we're here.
            pass

        transcript = None
        try:
            raw_transcript = await chat_exporter.export(interaction.channel)
            transcript = discord.File(
                io.BytesIO(raw_transcript.encode()), filename=f"transcript-{interaction.channel.id}.html",
            )
        except Exception:
            pass

        await interaction.channel.delete()

        logs_channel = interaction.guild.get_channel(self.client.config["channel_id"]["ticket_logs"])

        user = interaction.guild.get_member(user_id)
        user_msg = f"**User**: <@{user}>\n"
        if user is not None:
            user_msg = f"**User**: <@{user.id}> / {user.name}#{user.discriminator}\n"

        closer = interaction.user
        embed_log = discord.Embed(
            title="Ticket",
            description=f"{user_msg}"
                        f"**Closed by**: {closer.mention} / {closer.name}#{closer.discriminator}\n"
                        f"**Category**: {category}\n"
                        f"**Transcript**: *see attachments*\n",
            color=self.client.color,
            timestamp=discord.utils.utcnow()
        )

        if transcript is not None:
            await logs_channel.send(embed=embed_log, file=transcript)
        else:
            await logs_channel.send(embed=embed_log)

    """ Skill Evaluation commands. """

    skill_group = app_commands.Group(
        name="skill", description="Manage skills for users.",
        default_permissions=discord.Permissions(administrator=True)
    )

    @skill_group.command(name="add")
    async def skill_add(self, interaction: discord.Interaction, user: discord.Member, skill: str,
                        stars: Literal["1", "2", "3"]):
        """Add a skill to a user."""
        if skill not in self.client.config["tickets"]:
            return await interaction.response.send_message("This skill does not exist.", ephemeral=True)

        main_skill_role = interaction.guild.get_role(
            self.client.config["role_id"][f"{skill.lower()}_developer"]["main"]
        )
        stars_skill_role = interaction.guild.get_role(
            self.client.config["role_id"][f"{skill.lower()}_developer"][stars]
        )

        await user.add_roles(main_skill_role, stars_skill_role)

        await interaction.response.send_message(
            f"You successfully added the {skill} skill with {stars} ⭐ to {user.mention}.", ephemeral=True
        )

    @skill_group.command(name="remove")
    async def skill_remove(self, interaction: discord.Interaction, user: discord.Member, skill: str):
        """Remove a skill from a user."""
        if skill not in self.client.config["tickets"]:
            return await interaction.response.send_message("This skill does not exist.", ephemeral=True)

        roles_to_remove = []
        main_skill_role = interaction.guild.get_role(
            self.client.config["role_id"][f"{skill.lower()}_developer"]["main"]
        )

        roles_to_remove.append(main_skill_role)
        for x in range(1, 4):
            roles_to_remove.append(
                interaction.guild.get_role(self.client.config["role_id"][f"{skill.lower()}_developer"][f"{x}"])
            )

        await user.remove_roles(*roles_to_remove)

        await interaction.response.send_message(
            f"You successfully removed the {skill} skill from {user.mention}.", ephemeral=True
        )

    @skill_add.autocomplete("skill")
    @skill_remove.autocomplete("skill")
    async def skill_autocomplete(self, interaction: discord.Interaction, current: str) -> List[Choice[str]]:
        skills = list(self.client.config["tickets"].keys())
        return [Choice(name=skill, value=skill) for skill in skills if current.lower() in skill.lower()]


async def setup(client):
    await client.add_cog(Tickets(client))

import logging
import os
import re
import json
from datetime import datetime
import asyncio
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Telegram Bot credentials
BOT_TOKEN = 
CHAT_ID = 

# Base URL for scraping
BASE_URL = "https://www.olx.pl/elektronika/telefony/smartfony-telefony-komorkowe/iphone/?search%5Border%5D=created_at:desc"

# File paths
MODELS_FILE = "iphone_models.json"
SEEN_POSTS_FILE = "seen_posts.json"
STATUS_FILE = "bot_status.json"

# Default status
DEFAULT_STATUS = {
    "running": False,
    "last_check": None,
    "check_interval": 120,  # 2 minutes in seconds
    "total_posts_found": 0,
    "models_tracked": []
}


class JSONHandler:
    @staticmethod
    def load(file_path, default_value=None):
        """Load data from a JSON file or return default value if file doesn't exist."""
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as file:
                    return json.load(file)
            return default_value if default_value is not None else {}
        except Exception as e:
            logger.error(f"Error loading {file_path}: {e}")
            return default_value if default_value is not None else {}

    @staticmethod
    def save(file_path, data):
        """Save data to a JSON file."""
        try:
            with open(file_path, 'w', encoding='utf-8') as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"Error saving {file_path}: {e}")
            return False


class OLXScraper:
    def __init__(self):
        self.driver = None
        self.stop_requested = False
        
    async def initialize(self):
        """Initialize Selenium WebDriver with Chrome."""
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        
    async def close(self):
        """Close the WebDriver."""
        if self.driver:
            try:
                self.driver.quit()
                print("WebDriver closed")
            except Exception as e:
                logger.error(f"Error closing driver: {e}")
    
    async def scrape(self, bot, models, seen_posts, status):
        """Scrape OLX.pl for iPhone listings based on tracked models."""
        if not self.driver:
            await self.initialize()
        
        try:
            # Initial page load
            print("\n" + "="*50)
            print(f"🔍 STARTING SEARCH FOR IPHONE MODELS")
            print("="*50)
            
            # Clear cookies and storage to prevent stale cache
            print("Clearing cookies and browser storage...")
            self.driver.delete_all_cookies()
            self.driver.execute_script("localStorage.clear(); sessionStorage.clear();")
            
            # Load the page
            self.driver.get(BASE_URL)
            
            # Refresh the page to ensure a fresh load
            print("Refreshing page...")
            self.driver.refresh()
            
            # Wait for the posts to load
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-cy='l-card']"))
            )
            
            # Scroll to the bottom to trigger dynamic content loading
            print("Scrolling to load dynamic content...")
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            await asyncio.sleep(2)  # Short delay to allow content to load
            
            # Get all posts
            posts = self.driver.find_elements(By.CSS_SELECTOR, "[data-cy='l-card']")
            print(f"Found {len(posts)} posts on OLX")
            
            # Debug: Log the first 5 listing titles
            print("\n🔍 DEBUG: First 5 listing titles:")
            for i, post in enumerate(posts[:5]):
                try:
                    title = post.find_element(By.CSS_SELECTOR, "h4, h6").text.strip()
                    print(f"  {i+1}. {title}")
                except Exception as e:
                    print(f"  {i+1}. Error retrieving title: {e}")
            print()
            
            # Update status
            status["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            JSONHandler.save(STATUS_FILE, status)
            
            # Process posts
            checked, matched, already_seen = 0, 0, 0
            
            for post in posts[:30]:  # Limit to first 30 posts
                if self.stop_requested:
                    break
                    
                try:
                    checked += 1
                    
                    # Extract post ID and link
                    post_link = post.find_element(By.CSS_SELECTOR, "a").get_attribute("href")
                    post_id = re.search(r"ID([a-zA-Z0-9]+)\.html", post_link)
                    post_id = post_id.group(1) if post_id else post_link
                    
                    # Skip if already seen
                    if post_id in seen_posts:
                        already_seen += 1
                        continue
                    
                    # Extract title
                    try:
                        title = post.find_element(By.CSS_SELECTOR, "h4, h6").text.strip()
                    except:
                        continue
                    
                    # Extract price
                    try:
                        price_element = post.find_element(By.CSS_SELECTOR, "p[data-testid='ad-price']")
                        price_text = price_element.text.strip()
                        # Extract numeric price
                        price_match = re.search(r'(\d[\d\s]*)', price_text)
                        if price_match:
                            price_str = price_match.group(1).replace(" ", "")
                            price_value = int(price_str)
                        else:
                            price_value = 0  # If price can't be extracted
                    except:
                        price_text = "Price not found"
                        price_value = 0
                    
                    # Check if the post matches any of the tracked models
                    matching_model = None
                    max_price = None
                    
                    for model_data in models:
                        # Handle both old format (string) and new format (dict)
                        if isinstance(model_data, str):
                            model_name = model_data
                            model_max_price = None
                        else:
                            model_name = model_data.get("model", "")
                            model_max_price = model_data.get("max_price")
                        
                        model_regex = re.compile(model_name, re.IGNORECASE)
                        if model_regex.search(title):
                            # Check price constraint if it exists
                            if model_max_price is not None and price_value > model_max_price:
                                print(f"Price too high for {model_name}: {price_value} > {model_max_price}")
                                continue
                                
                            matching_model = model_name
                            max_price = model_max_price
                            break
                    
                    if matching_model:
                        matched += 1
                        # Extract location and time
                        try:
                            location_time = post.find_elements(By.CSS_SELECTOR, "p[data-testid='location-date']")
                            location_time_text = location_time[0].text if location_time else "Unknown location and time"
                        except:
                            location_time_text = "Unknown location and time"
                        
                        # Check for duplicates
                        duplicate = any(seen_data.get("link") == post_link for seen_id, seen_data in seen_posts.items())
                        
                        if not duplicate:
                            # Create message
                            price_info = f"{price_text}"
                            if max_price:
                                price_info += f" (Max: {max_price} zł)"
                                
                            message = (
                                f"🔔 <b>New iPhone Listing</b> 🔔\n\n"
                                f"📱 <b>Model:</b> {matching_model}\n"
                                f"💰 <b>Price:</b> {price_info}\n"
                                f"📍 <b>Details:</b> {location_time_text}\n"
                                f"🔗 <b>Link:</b> <a href=\"{post_link}\">{title}</a>\n\n"
                                f"<b>Title:</b> {title}"
                            )
                            
                            # Send notification
                            await bot.send_message(
                                chat_id=CHAT_ID,
                                text=message,
                                parse_mode='HTML',
                                disable_web_page_preview=False
                            )
                            
                            # Mark as seen and update stats
                            seen_posts[post_id] = {
                                "title": title,
                                "model": matching_model,
                                "price": price_text,
                                "link": post_link,
                                "found_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }
                            status["total_posts_found"] += 1
                            
                            # Save updated data
                            JSONHandler.save(SEEN_POSTS_FILE, seen_posts)
                            JSONHandler.save(STATUS_FILE, status)
                            
                            print(f"✅ Match found: {matching_model} - {price_text}")
                
                except Exception as e:
                    logger.error(f"Error processing post: {e}")
                    continue
                    
            # Print summary
            print("-"*50)
            print(f"SCAN SUMMARY:")
            print(f"- Posts checked: {checked}")
            print(f"- Posts already seen: {already_seen}")
            print(f"- New matching posts: {matched}")
            print(f"- Total matches found (all time): {status['total_posts_found']}")
            print("-"*50)
            
            return True
                
        except Exception as e:
            logger.error(f"Error during scraping: {e}")
            return False


class OLXScraperBot:
    def __init__(self):
        self.scraper = OLXScraper()
        self.running = False
        self.task = None
    
    def load_models(self):
        return JSONHandler.load(MODELS_FILE, [])
    
    def save_models(self, models):
        return JSONHandler.save(MODELS_FILE, models)
    
    def load_seen_posts(self):
        return JSONHandler.load(SEEN_POSTS_FILE, {})
    
    def load_status(self):
        status = JSONHandler.load(STATUS_FILE, DEFAULT_STATUS)
        # Update models_tracked from models file
        status["models_tracked"] = self.load_models()
        return status
    
    def save_status(self, status):
        return JSONHandler.save(STATUS_FILE, status)
    
    async def scraper_job(self, bot):
        """Background job for periodic scraping."""
        models = self.load_models()
        seen_posts = self.load_seen_posts()
        status = self.load_status()
        
        print("\n" + "*"*50)
        print("🤖 SCRAPER BOT ACTIVATED 🤖")
        print(f"Starting scraper with {len(models)} models to track")
        print("*"*50)
        
        while self.running:
            # Reload models each time to pick up any changes
            models = self.load_models()
            
            if models:
                print(f"\n🔄 Starting scraping cycle at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                await self.scraper.scrape(bot, models, seen_posts, status)
            else:
                print("⚠️ No models to track. Skipping scraping.")
            
            # Exit if stopped
            if not self.running:
                break
                
            # Wait for the next check interval
            status = self.load_status()
            print(f"\n⏱️ Waiting {status['check_interval']} seconds until next cycle")
            try:
                # Wait with regular checks for stop signal
                for _ in range(status['check_interval']):
                    if not self.running:
                        break
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
        
        # Clean up resources
        await self.scraper.close()
        print("\n" + "*"*50)
        print("🛑 SCRAPER BOT STOPPED 🛑")
        print("*"*50)
    
    async def start(self, bot):
        """Start the scraper."""
        if self.running:
            return False
            
        self.running = True
        # Reset scraper stop flag
        self.scraper.stop_requested = False
        
        # Start scraper task
        self.task = asyncio.create_task(self.scraper_job(bot))
        
        # Update status
        status = self.load_status()
        status["running"] = True
        self.save_status(status)
        
        return True
    
    async def stop(self):
        """Stop the scraper."""
        if not self.running:
            return False
            
        self.running = False
        self.scraper.stop_requested = True
        
        # Cancel the task if it exists
        if self.task and not self.task.done():
            try:
                self.task.cancel()
                await asyncio.wait_for(self.task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        
        # Update status
        status = self.load_status()
        status["running"] = False
        self.save_status(status)
        
        return True


# Create a global instance of the bot
scraper_bot = OLXScraperBot()


# Command handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message when the command /start is issued."""
    user = update.effective_user
    welcome_message = (
        f"Hello {user.first_name}! 👋\n\n"
        f"I'm your iPhone OLX Scraper Bot. I'll help you track new iPhone listings on OLX.pl.\n\n"
        f"Commands you can use:\n"
        f"• /start - Show this welcome message\n"
        f"• /add <model> [max_price] - Add an iPhone model to track (with optional price limit)\n"
        f"• /delete - Delete a tracked model\n"
        f"• /list - Show all tracked models\n"
        f"• /status - Check the bot's status\n"
        f"• /run - Start the scraper\n"
        f"• /stop - Stop the scraper\n\n"
        f"Let's start by adding an iPhone model to track using /add command!"
    )
    await update.message.reply_text(welcome_message)


async def add_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add an iPhone model to track, with optional max price."""
    if not context.args:
        await update.message.reply_text(
            "Please specify an iPhone model to track.\n"
            "Examples:\n"
            "• /add iPhone 13 Pro\n"
            "• /add iPhone 11 500 (to set max price of 500 zł)"
        )
        return
    
    models = scraper_bot.load_models()
    
    # Check if the last argument is a number (max price)
    if len(context.args) > 1 and context.args[-1].isdigit():
        max_price = int(context.args[-1])
        model_name = ' '.join(context.args[:-1])
        model_data = {"model": model_name, "max_price": max_price}
    else:
        model_name = ' '.join(context.args)
        model_data = {"model": model_name}
    
    # Check if model already exists
    for existing_model in models:
        if isinstance(existing_model, str) and existing_model == model_name:
            models.remove(existing_model)
        elif isinstance(existing_model, dict) and existing_model.get("model") == model_name:
            models.remove(existing_model)
    
    # Add the new model
    models.append(model_data)
    
    if scraper_bot.save_models(models):
        # Update status file as well
        status = scraper_bot.load_status()
        status["models_tracked"] = models
        scraper_bot.save_status(status)
        
        price_info = f" with max price {max_price} zł" if "max_price" in model_data else ""
        await update.message.reply_text(f"✅ Added '{model_name}'{price_info} to tracked models.\nTotal models tracked: {len(models)}")
    else:
        await update.message.reply_text("❌ Failed to add model. Please try again.")


async def delete_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete an iPhone model from tracking list."""
    models = scraper_bot.load_models()
    if not models:
        await update.message.reply_text("No models are currently being tracked.")
        return
    
    keyboard = []
    for model in models:
        if isinstance(model, str):
            display_name = model
            callback_data = f"delete_{model}"
        else:
            display_name = model.get("model", "")
            if "max_price" in model:
                display_name += f" (Max: {model['max_price']} zł)"
            callback_data = f"delete_{model['model']}"
        
        keyboard.append([InlineKeyboardButton(f"❌ {display_name}", callback_data=callback_data)])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select a model to delete:", reply_markup=reply_markup)


async def list_models_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all tracked iPhone models."""
    models = scraper_bot.load_models()
    
    if not models:
        await update.message.reply_text("No models are currently being tracked. Add one with /add command.")
        return
    
    message = "📱 <b>Tracked iPhone Models:</b>\n\n"
    for i, model in enumerate(models, 1):
        if isinstance(model, str):
            message += f"{i}. {model}\n"
        else:
            model_name = model.get("model", "")
            if "max_price" in model:
                message += f"{i}. {model_name} (Max: {model['max_price']} zł)\n"
            else:
                message += f"{i}. {model_name}\n"
    
    message += f"\nTotal: {len(models)} models"
    
    # Add button to delete models
    keyboard = [[InlineKeyboardButton("Delete Models", callback_data="show_delete")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, parse_mode='HTML', reply_markup=reply_markup)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check the bot's status."""
    status = scraper_bot.load_status()
    models = scraper_bot.load_models()
    
    status_emoji = "✅" if status["running"] else "❌"
    last_check = status["last_check"] if status["last_check"] else "Never"
    
    message = (
        f"<b>🤖 Bot Status</b>\n\n"
        f"• <b>Running:</b> {status_emoji} {status['running']}\n"
        f"• <b>Last Check:</b> {last_check}\n"
        f"• <b>Check Interval:</b> {status['check_interval']} seconds\n"
        f"• <b>Total Posts Found:</b> {status['total_posts_found']}\n"
        f"• <b>Models Tracked:</b> {len(models)}\n"
    )
    
    # Add control buttons
    keyboard = []
    if status["running"]:
        keyboard.append([InlineKeyboardButton("🛑 Stop Bot", callback_data="stop_bot")])
    else:
        keyboard.append([InlineKeyboardButton("▶️ Start Bot", callback_data="start_bot")])
    
    keyboard.append([InlineKeyboardButton("📋 List Models", callback_data="list_models")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, parse_mode='HTML', reply_markup=reply_markup)


async def run_bot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the scraper."""
    models = scraper_bot.load_models()
    
    if not models:
        await update.message.reply_text("❌ No models to track. Add models first using /add command.")
        return
    
    if await scraper_bot.start(context.bot):
        await update.message.reply_text("✅ Scraper started. I'll notify you when new matching iPhone listings appear.")
    else:
        await update.message.reply_text("⚠️ Scraper is already running.")


async def stop_bot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop the scraper."""
    if await scraper_bot.stop():
        await update.message.reply_text("✅ Scraper stopped.")
    else:
        await update.message.reply_text("⚠️ Scraper is not running.")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "start_bot":
        if await scraper_bot.start(context.bot):
            await query.edit_message_text("✅ Scraper started. I'll notify you when new matching iPhone listings appear.")
        else:
            await query.edit_message_text("⚠️ Scraper is already running.")
    
    elif data == "stop_bot":
        if await scraper_bot.stop():
            await query.edit_message_text("✅ Scraper stopped.")
        else:
            await query.edit_message_text("⚠️ Scraper is not running.")
    
    elif data == "list_models":
        await list_models_command(update, context)
    
    elif data == "show_delete":
        await delete_model_command(update, context)
    
    elif data.startswith("delete_"):
        model_name = data[7:]  # Remove "delete_" prefix
        models = scraper_bot.load_models()
        
        # Find and remove the model
        removed = False
        for model in list(models):  # Make a copy to avoid modification issues
            if (isinstance(model, str) and model == model_name) or \
               (isinstance(model, dict) and model.get("model") == model_name):
                models.remove(model)
                removed = True
        
        if removed and scraper_bot.save_models(models):
            # Update status file as well
            status = scraper_bot.load_status()
            status["models_tracked"] = models
            scraper_bot.save_status(status)
            
            await query.edit_message_text(f"✅ Removed '{model_name}' from tracked models.\nTotal models tracked: {len(models)}")
        else:
            await query.edit_message_text(f"❌ Failed to remove model. Please try again.")


def main():
    """Run the bot."""
    # Create necessary files if they don't exist
    if not os.path.exists(MODELS_FILE):
        JSONHandler.save(MODELS_FILE, [])
    
    if not os.path.exists(SEEN_POSTS_FILE):
        JSONHandler.save(SEEN_POSTS_FILE, {})
    
    if not os.path.exists(STATUS_FILE):
        JSONHandler.save(STATUS_FILE, DEFAULT_STATUS)
    
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("add", add_model_command))
    application.add_handler(CommandHandler("delete", delete_model_command))
    application.add_handler(CommandHandler("list", list_models_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("run", run_bot_command))
    application.add_handler(CommandHandler("stop", stop_bot_command))
    
    # Add callback query handler
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Start the Bot - Updated for v20+ style
    print("Starting the bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage , trim_messages
from pathlib import Path
import os
import time
import urllib.request
import json

from rich.text import Text

from rich.syntax import Syntax
from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.containers import Vertical, Horizontal, ScrollableContainer, Container
from textual.widgets import TextArea, RichLog, Header, Footer, LoadingIndicator , Select, Input, Label, Button
from textual.binding import Binding
from textual import work
from rich.panel import Panel
from rich.markdown import Markdown as RichMarkdown
from .tools import tools
from dotenv import load_dotenv, set_key, unset_key, dotenv_values




# config files path 

config_dir = Path.home() / "mylo-config"
ENV_FILE = str(config_dir/ ".env")
PROMPT_FILE = config_dir / "SYSTEM_PROMPT.md"




# Base identity string
base_identity = """ 
# RULES:

0. Be friendly and give structured readable outputs in Markdown format (with closed ``` ``` everytime if present). Small talk is fine 

1. If asked about a repo (or to analyse a repo), reply by urself if you know the repo or fetch and analyse the README.md file if you dont know about the repo (only read small portion of the README.md to get an understanding about the repo)

2. only use save_string_to_file function if user say to save file.

3. If no provider name is specified, take github as the provider

4. Dont call the same tool more than once per query, if it returns at first call. Do not call the same tool more than once even for verifying

5. If the user asks to SEE something (show me, list, what are, find) → show the raw tool output

6. If the user wants to UNDERSTAND something (explain, analyse, summarise, what does) → analyse the tool output and give an explanation

7. If the user asks to DO something (fetch, get, find) → show the raw result directly

8. ALWAYS make clear what way your decision are made by starting your response with either:

  [DIRECT] if showing raw output

  [ANALYSIS] if processing and explaining

**IMP**: NEVER REDUCE THE OUTPUT OF ANY TOOL. SHOW THE RAW RESULT AS IS.

"""



def dynamic_llm(model_provider, model_name, api_key):
    """
    Dynamically give the llm object based on the model provider, model name and api key
    """
    # Fail immediately if model name or api key is not given
    if not api_key or not model_name:
        return None
        
    try:
        # Returs the required llm objects
        if model_provider == "groq":
            return ChatGroq(model=model_name, groq_api_key=api_key)
            
        elif model_provider == "openai":
            return ChatOpenAI(model=model_name, openai_api_key=api_key)
            
        elif model_provider == "anthropic":
            return ChatAnthropic(model=model_name, anthropic_api_key=api_key)
            
        elif model_provider == "google":
            return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key)
            
        else:
            # Fallback block triggered if an unrecognized provider string is supplied
            return None
            
    except Exception:
        # Catch instantiation faults (e.g., missing SDK packages or invalid parameters) 
        # to ensure the runtime application can recover gracefully without a hard crash
        return None




def save_profile_to_env(self, nickname: str, provider: str, model_name: str, api_key: str, memory_limit: int = 2048):
    """Save All Model Profiles in an env file inside ~/mylo-config"""

    # Save the provider name (e.g., 'openai', 'anthropic'). 
    # We use .lower() to ensure consistent lookups later, regardless of how the user typed it.
    set_key(ENV_FILE, f"PROFILE_{nickname}_PROVIDER", provider.lower())
    
    # Save the model name (e.g., 'gpt-4', 'claude-3-opus')
    set_key(ENV_FILE, f"PROFILE_{nickname}_MODEL_NAME", model_name)
    
    # Save the user's API key for this specific provider
    set_key(ENV_FILE, f"PROFILE_{nickname}_API_KEY", api_key)
    
    # Save the memory limit. 
    set_key(ENV_FILE, f"PROFILE_{nickname}_MEMORY_LIMIT", str(memory_limit))
    
    # Save the model profile name that is active 
    set_key(ENV_FILE, "ACTIVE_PROFILE", nickname)
    
    # Reload active model
    self.reload_active_model()



def trim_history(messages: list, memory_limit: int, system_prompt: str, token_model) -> list:
    """
    Uses LangChain's official tokenizer library to dynamically trim chat context.
    Guarantees that the System Prompt is anchored at the top and never cut.
    """

    full_payload = [SystemMessage(content=system_prompt)] + messages
    
    try:
        # DYNAMIC TOKEN COUNTER SETUP:
        # 1. If we received a LangChain LLM object, use its built-in cross-provider counter.
        # 2. If we received something else (like a string), fall back to safe character based estimation.
        if hasattr(token_model, "get_num_tokens_from_messages"):
            counter = token_model.get_num_tokens_from_messages
        else:
            def counter(msgs):
                # Safe fallback without needing external imports
                return sum(len(m.content) // 4 for m in msgs if hasattr(m, 'content'))

        return trim_messages(
            full_payload,
            memory_limit=memory_limit,
            strategy="last",
            token_counter=counter, 
            include_system=True,
        )
    except Exception:
        # FALLBACK MECHANISM: use character based approximation
        fallback_tokens = 0       
        retained = []             
        sys_msg = SystemMessage(content=system_prompt)
        
        fallback_tokens += len(sys_msg.content) // 4
        
        for msg in reversed(messages):
            msg_tokens = len(msg.content) // 4 + 1
            if fallback_tokens + msg_tokens > memory_limit:
                break
                
            retained.insert(0, msg)
            fallback_tokens += msg_tokens
            
        return [sys_msg] + retained




def format_compact_tokens(count: int) -> str:
        """
        Converts raw token counts into clean, human-readable format.
        Example: 842 -> "842", 1500 -> "1.5k", 100000 -> "100k", 2450000 -> "2.5M"
        """
        if count < 1000:
            return str(count)
    
        # For thousands (up to 999,999)
        if count < 1000000:
            value = count / 1000
            return f"{value:.1f}k".replace(".0k", "k")
        
        # For millions (1,000,000+)
        value = count / 1000000
        return f"{value:.1f}M".replace(".0M", "M")



def get_model_token_prices(model_name: str, provider_hint: str = "") -> list[float]:
    """
    Fetches price per token from litellm github repo
    """
    LITELLM_PRICING_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
    CACHE_FILE = os.path.expanduser("~/.mylo_tokencost_cache.json")
    CACHE_TTL = 86400
    
    matrix = {}

    # 1. Load from local cache
    if os.path.exists(CACHE_FILE):
        if time.time() - os.path.getmtime(CACHE_FILE) < CACHE_TTL:
            try:
                with open(CACHE_FILE, "r") as f:
                    matrix = json.load(f)
            except Exception:
                pass

    if not matrix:
        try:
            with urllib.request.urlopen(LITELLM_PRICING_URL, timeout=5) as response:
                matrix = json.loads(response.read().decode())
                with open(CACHE_FILE, "w") as f:
                    json.dump(matrix, f)
        except Exception:
            if os.path.exists(CACHE_FILE):
                try:
                    with open(CACHE_FILE, "r") as f:
                        matrix = json.load(f)
                except Exception:
                    pass

    model_clean = model_name.strip().lower()
    model_data = {}

    # 3. Lookup: Try an exact match first
    if model_clean in matrix:
        model_data = matrix[model_clean]
    elif model_clean and model_clean != "unknown":
        # Fuzzy match (handles layout variants like mapping "gpt-4o" to "openai/gpt-4o")
        for key, data in matrix.items():
            key_lower = key.lower()
            if (model_clean in key_lower or key_lower in model_clean) and "input_cost_per_token" in data:
                model_data = data
                break

    #If missing, default to typical industry rates based on provider context
    if not model_data or model_clean == "unknown":
        provider = provider_hint.lower()
        if "openai" in provider or "gpt" in model_clean:
            return [2.5e-06, 10.0e-06]    # Generic GPT-4o Standard Baseline
        elif "anthropic" in provider or "claude" in model_clean:
            return [3.0e-06, 15.0e-06]    # Generic Claude-3.5-Sonnet Baseline
        elif "google" in provider or "gemini" in model_clean:
            return [0.075e-06, 0.3e-06]   # Generic Gemini Flash Baseline
        else:
            return [0.0, 0.0]

    return [round(model_data.get("input_cost_per_token", 0.0), 8), round(model_data.get("output_cost_per_token", 0.0),8)]






class GitTokenModal(ModalScreen):
    """A direct write, modify, and delete TUI screen for git provider (Github and Gitlab) API tokens."""
    
    CSS = """
GitTokenModal {
    align: center middle;
    background: rgba(0, 0, 0, 0.6);
}

#modal-container {
    width: 60;                  
    height: 19;                 
    border: thick $primary;
    background: $surface;
    padding: 0 2;               
}

#modal-title {
    margin-top: 1;              
    text-align: center;
}

.field-label {
    margin-top: 0;             
    text-style: bold;
    color: $text-muted;
}

Select, Input {
    margin-bottom: 0;           
    height: 3;                  
}

#button-bucket {
    margin-top: 1;              
    align: right middle;
    height: 3;
}

#button-bucket Button {
    margin-left: 1;             
}
"""

    def compose(self) -> ComposeResult:
        
        # Vertical Container: this act as a column. All widgets yielded inside it and will be staked from top to bottom
        with Vertical(id="modal-container"):

            #The header text for the window
            yield Label("Manage API Tokens", id="modal-title")

            #The hoster dropdown (Includes Github and Gitlab)            
            yield Label("Select Provider:", classes="field-label")
            
            yield Select(
                options=[("GitHub", "github"), ("GitLab", "gitlab")],
                value="github", # default to github
                id="provider-select" # ID used to read this widget's value later in the code
            )
            
            yield Label("Token Value:", classes="field-label")

            # Input box to type the token 
            yield Input(
                password=True, #masks the token while typing
                placeholder="Paste token here to save/overwrite...", 
                id="token-input" #ID used to read the widget's value later in the code
            )
            
            # Horizontal Container: This act as a row. The buttons will be placed side by side 
            with Horizontal(id="button-bucket"):
                yield Button("Cancel", variant="default", id="cancel-btn")
                yield Button("Delete Existing", variant="error", id="delete-btn")
                yield Button("Save", variant="success", id="save-btn")

    
    # Textual's event handler that fires automatically whenever any button is pressed
    def on_button_pressed(self, event: Button.Pressed) -> None:

        # Close the modal window. Passing 'None' tells the parent screen that the user cancelled.
        if event.button.id == "cancel-btn":
            self.dismiss(None)
            return

        # --- Save Logic ---    
        elif event.button.id == "save-btn":

            # Get the provider (or hoster) and the token value
            provider = self.query_one("#provider-select").value
            token_val = self.query_one("#token-input").value.strip()
            
            # Validate that the user actually picked a provider from the dropdown
            # (Select.NULL means the dropdown is untouched/default)
            if not provider or provider is Select.NULL:
                self.notify("Please select a provider")
                return
            
            # Check whether the token isn't empty
            elif not token_val:
                self.notify("Please enter a valid token")
                return 
            
            # Create an env variable
            env_key = f"{str(provider).upper()}_TOKEN"
            
            # Read existing .env values to check the old state
            env_vars = dotenv_values(ENV_FILE)
            old_val = env_vars.get(env_key)

            try:
                # Write the new token to the .env configuration file
                set_key(ENV_FILE, env_key, token_val)
                
                # ONLY trigger notification if token already existed and is being changed
                if old_val and old_val != token_val:
                    self.notify(f"{str(provider).title()} token updated successfully")
                
                if hasattr(self.app, "reload_active_model"):
                    self.app.reload_active_model()
                    
            except Exception as e:
                # Catch file permission errors or disk issues and show them to the user
                self.notify(f"Failed to save token: {e}")
            
            #close the window
            self.dismiss(True)

        # --- Delete Logic ---
        elif event.button.id == "delete-btn":

            provider = self.query_one("#provider-select").value

            #revalidate the provider selection
            if not provider or provider is Select.NULL:
                self.notify("No provider selected.")
                return

            env_key = f"{str(provider).upper()}_TOKEN"
            env_vars = dotenv_values(ENV_FILE)
            
            # Since the variables names are saved as in uppercase
            file_keys = {k.upper(): k for k in env_vars}
            
            # Check if our target key exists in uppercase-mapped dict, and make sure it isn't just an empty string
            if env_key not in file_keys or not env_vars.get(file_keys[env_key]):
                self.notify(f"No existing token found for {str(provider).title()}.")
                return

            try:

                # passing the original casing (file_keys[env_key]) to unset_key
                unset_key(ENV_FILE, file_keys[env_key])

                # Clear the text input box in the UI 
                self.query_one("#token-input").value = ""
                
                # Reload the active model
                if hasattr(self.app, "reload_active_model"):
                    self.app.reload_active_model()
                    
            except Exception as e:
                self.notify(f"Failed to delete token: {e}")

            # close the window    
            self.dismiss(True)




class ConfigModal(ModalScreen):
    """ModalScreen to create and modify Model Profiles"""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Close"),
    ]

    CSS = """
    ConfigModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.6);
    }
    #modal-wrapper {
        width: 60;
        height: 75%;
        min-height: 12;
        max-height: 38;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #form-scroller {
        height: 1fr;
        scrollbar-gutter: stable;
    }
    .field-label {
        margin-top: 1;
        color: $text;
        text-style: bold;
    }
    .section-title {
        margin-top: 1;
        color: $accent;
        text-style: bold;
    }
    Input, Select {
        margin-bottom: 1;
        width: 100%;
    }
    #btn-row {
        margin-top: 1;
        height: 3;
        align: center middle;
    }
    Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:

        # Load the config env file 
        env_vars = dotenv_values(ENV_FILE)
        
        # Extract Profile nicknames
        saved_profile_names = sorted(list({
            key[8:-9] for key in env_vars if key.startswith("PROFILE_") and key.endswith("_PROVIDER")
        }))
        
        saved_profiles = [(name, name) for name in saved_profile_names]
        
        # Get the active profile name
        active_profile = env_vars.get("ACTIVE_PROFILE")

        # Tuple of AI providers for the dropdown
        providers = [
            ("Google Gemini", "google"),
            ("OpenAI", "openai"),
            ("Groq", "groq"),
            ("Anthropic", "anthropic"),
        ]

        with Vertical(id="modal-wrapper"):
            
            # ScrollableContainer ensures that if the form is taller than the user's terminal window,
            # they can scroll up and down 
            with ScrollableContainer(id="form-scroller"):

                # --- Quick Load ---
                if saved_profiles:
                    # Conditionally render this section ONLY if there are saved profiles in the .env file
                    yield Label("Quick Load Saved Profile:", classes="section-title")
                    yield Select(
                        saved_profiles,
                        prompt="-- Choose existing profile --",
                        id="quick-select",
                        
                        # Pre-select the active profile if it exists in the list,
                        # otherwise default to an empty/unselected state (Select.NULL)
                        value=active_profile if active_profile in saved_profile_names else Select.NULL
                    )

                # --- Create and edit Model Profile ---
                yield Label("Create /  Edit Model Profile:", classes="section-title")
                yield Label("Profile Name:", classes="field-label")
                yield Input(placeholder="e.g., MyGroqBot", id="profile-name-input")
                yield Label("Provider:", classes="field-label")
                yield Select(providers, prompt="Select provider...", id="provider-select")
                yield Label("Model Name:", classes="field-label")
                yield Input(placeholder="e.g., llama-3.3-70b-versatile", id="model-input")
                yield Label("Memory Limit:", classes="field-label")
                yield Input(placeholder="e.g., 2048", id="tokens-input")
                yield Label("API Key:", classes="field-label")

                # password = True will mask the api key while typing
                yield Input(placeholder="Enter API key...", password=True, id="api-input")
            
            # Buttons for save / activate, delete and cancel
            with Horizontal(id="btn-row"):
                yield Button("Save / Activate", variant="primary", id="btn-save")
                yield Button("Delete", variant="error", id="btn-delete")
                yield Button("Cancel", variant="default", id="btn-cancel")


    def on_mount(self) -> None:
        
        # Load the env config file
        env_vars = dotenv_values(ENV_FILE)

        # Check whether any Model Profile exist
        has_profiles = any(k.startswith("PROFILE_") and k.endswith("_PROVIDER") for k in env_vars)
        
        if has_profiles:
            try:
                #If profile exist, automatically focuses the "Quick Load" dropdown 
                self.query_one("#quick-select").focus()
            except Exception:
                # If the dropdown failed to render for some unexpected reason,
                # fall back to putting the focus on the first text input instead of crashing the app.
                self.query_one("#profile-name-input").focus()
        else:
            # If profile dont exist, automatically focus to "Profile Name"
            self.query_one("#profile-name-input").focus()
    
    # Textual event handler that fires every time the user's focus moves to a NEW widget 
    def on_focus(self, event) -> None:

        # Only trigger auto-scrolling for interactive form fields. 
        # Ignore focus changes on static text like Labels or Buttons.
        if isinstance(event.target, (Input, Select)):
            scroller = self.query_one("#form-scroller")
            if event.target.id == "api-input":
                
                # Force the scroller to go to 100% absolute bottom to ensure api key input box is fully visible.
                scroller.scroll_y = scroller.max_scroll_y
            else:

                # For all other inputs/dropdowns, tell the scroller to shift its view
                # animate = False make the jump / shift instant 
                scroller.scroll_to_widget(event.target, animate=False)

    
    # Textual event handler that fires whenever the user picks a new option from ANY Select (dropdown) widget
    def on_select_changed(self, event: Select.Changed) -> None:

        # Only run this logic if the changed dropdown is the "Quick Load" one
        # and the user actually selected a valid profile.
        if event.control.id == "quick-select" and event.value is not Select.NULL:

            # read the config env file
            env_vars = dotenv_values(ENV_FILE)

            # extract the profile name
            p_name = str(event.value)
            
            # Read variables safely with literal matching
            provider = env_vars.get(f"PROFILE_{p_name}_PROVIDER", "")
            model_name = env_vars.get(f"PROFILE_{p_name}_MODEL_NAME", "")
            memory_limit = env_vars.get(f"PROFILE_{p_name}_MEMORY_LIMIT", "2048")
            api_key = env_vars.get(f"PROFILE_{p_name}_API_KEY", "")
            

            # Find each Input widget by its ID and inject the loaded values into the text boxes.
            self.query_one("#profile-name-input", Input).value = p_name
            self.query_one("#model-input", Input).value = model_name
            self.query_one("#tokens-input", Input).value = memory_limit
            self.query_one("#api-input", Input).value = api_key
            

            #Update the provider dropdown to match the loaded profile
            if provider:
                self.query_one("#provider-select", Select).value = provider

    
    # Event handler that triggers when button clicks
    def on_button_pressed(self, event: Button.Pressed) -> None:

        # --- Close Logic --- 
        if event.button.id == "btn-cancel":
            self.dismiss(None)

        # --- Save Logic ---    
        elif event.button.id == "btn-save":
            self._submit()

        # --- Delete Logic ---     
        elif event.button.id == "btn-delete":
            self._delete_profile()

    def on_input_submitted(self, event: Input.Submitted) -> None:

        # Stops this event from being passed to parent widgets, which could trigger 
        # unwanted default behaviors or other event listeners higher up in the app.
        event.stop()

        # Trigger save when pressing "Enter" when the api key input box is focused
        if event.control.id == "api-input":
            self._submit()

    
    
    def _submit(self) -> None:

        # 1. Gather all current values from the UI inputs, removing any accidental whitespace
        p_name = self.query_one("#profile-name-input", Input).value.strip()

        # Safely handle the provider dropdown: if nothing is selected, default to an empty string 
        provider_sel = self.query_one("#provider-select", Select)
        provider = provider_sel.value if provider_sel.value is not Select.NULL else ""
        
        
        model = self.query_one("#model-input", Input).value.strip()
        tokens_raw = self.query_one("#tokens-input", Input).value.strip()
        key = self.query_one("#api-input", Input).value.strip()
        
        # Read the config env file
        env_vars = dotenv_values(ENV_FILE)

        # ─── QUICK SWITCH SHORTCUT ───────────────────────────
        # If the user only types a profile name and leaves the rest blank,
        # check if that profile already exists. If it does, just switch to it and close.
        if p_name and not model and not key:
            if f"PROFILE_{p_name}_PROVIDER" in env_vars:
                set_key(ENV_FILE, "ACTIVE_PROFILE", p_name)
                if hasattr(self.app, "reload_active_model"):
                    self.app.reload_active_model()
                self.dismiss({"action": "switched", "name": p_name})
                return

        # Ensure all field have content
        if not all([p_name, provider, model, tokens_raw, key]):
            self.notify("Please fill in all fields.")
            return

        # Ensure whether the memmory limit is an integer
        try:
            memory_limit = int(tokens_raw)
        except ValueError:
            self.notify("Memory Limit must be an Integer.")

            # Automatically put the cursor back into the tokens field so the user can fix it easily
            self.query_one("#tokens-input").focus()
            return

        # ─── TRACK API KEY UPDATE ───────────────────────────

        old_key = env_vars.get(f"PROFILE_{p_name}_API_KEY")
        key_notification = None

        # Check if this is a completely new profile
        is_new_profile = f"PROFILE_{p_name}_PROVIDER" not in env_vars

        # If the profile already exists, check whether its key is changed
        if not is_new_profile and old_key != key:
            if not old_key:
                key_notification = "New API Key added"
            else:
                key_notification = "API Key updated"

        # Write updates to the config env file
        set_key(ENV_FILE, f"PROFILE_{p_name}_PROVIDER", str(provider).lower())
        set_key(ENV_FILE, f"PROFILE_{p_name}_MODEL_NAME", model)
        set_key(ENV_FILE, f"PROFILE_{p_name}_MEMORY_LIMIT", str(memory_limit))
        set_key(ENV_FILE, f"PROFILE_{p_name}_API_KEY", key)
        set_key(ENV_FILE, "ACTIVE_PROFILE", p_name)

        # Make this profile as the active one
        if hasattr(self.app, "reload_active_model"):
            self.app.reload_active_model()

        # Only notify if an API key change took place
        if key_notification:
            self.notify(key_notification, title=f"Profile '{p_name}' Modified")

        # dismiss the modal and pass a dict back to the parent screen.
        # This allows the parent screen to immediately update its UI with the new model data
        # without having to re-read the env file.
        self.dismiss({
            "action": "saved",
            "name": p_name,
            "model": model,
            "data": {
                "provider": str(provider),
                "model_name": model,
                "memory_limit": memory_limit,
                "api_key": key,
            },
        })


    def _delete_profile(self) -> None:
        """Removes model profile data safely from the env file"""

        # Grab the profile name from the input box, stripping any accidental whitespace
        p_name = self.query_one("#profile-name-input", Input).value.strip()
        
        # Read the current state of env file
        env_vars = dotenv_values(ENV_FILE)

        # Stop if the input is empty, OR if the profile name doesn't exist in the env file.
        if not p_name or f"PROFILE_{p_name}_PROVIDER" not in env_vars:
            self.notify(f"Profile '{p_name}' does not exist.")
            return

        # Remove all 4 keys associated with this specific profile from the env file
        unset_key(ENV_FILE, f"PROFILE_{p_name}_PROVIDER")
        unset_key(ENV_FILE, f"PROFILE_{p_name}_MODEL_NAME")
        unset_key(ENV_FILE, f"PROFILE_{p_name}_MEMORY_LIMIT")
        unset_key(ENV_FILE, f"PROFILE_{p_name}_API_KEY")

        # Check if the profile deleted was marked as "active"
        if env_vars.get("ACTIVE_PROFILE") == p_name:

            # Re-read fresh file state after deletions
            updated_vars = dotenv_values(ENV_FILE)

            # Re-use the exact same parsing logic from compose() to find profiles
            remaining = sorted(list({
                k[8:-9] for k in updated_vars if k.startswith("PROFILE_") and k.endswith("_PROVIDER")
            }))


            if remaining:
                # If there are other profiles left, automatically promote the first in terms of alphebatic order as the active profile
                set_key(ENV_FILE, "ACTIVE_PROFILE", remaining[0])
            else:
                # If this was the VERY LAST profile in the app, remove the 
                # ACTIVE_PROFILE key entirely so the app doesn't point to a ghost profile
                unset_key(ENV_FILE, "ACTIVE_PROFILE")

        # Reload active model
        if hasattr(self.app, "reload_active_model"):
            self.app.reload_active_model()

        # Notify the user of successful profile deletion
        self.notify(f" Profile '{p_name}' Deleted", title="Profile Deleted")

        # Dismiss the modal, passing back a dict that tell the parent screen exactly which profile was just deleted
        self.dismiss({"action": "deleted", "name": p_name})






class Mylo(App):
    """The main app modal screen"""

    ENABLE_COMMAND_PALETTE = True

    BINDINGS = [
        Binding("ctrl+t", "exit_app", "Quit App", show=True),
        Binding("ctrl+r", "submit_message", "Send Message", show=True),
        Binding("ctrl+l", "open_config", "Model Profiles Config", show=True),
        Binding("ctrl+h", "new_session", "New Session", show=True),
        Binding("ctrl+b", "api_token", "Add API token", show=True)
    ]

    CSS = """
    Screen { background: $surface; padding: 0; }
    Header { width: 100%; }
    #chat-container { height: 1fr; margin: 1 2 0 2; }

    #query-container {
        dock: bottom; 
        layout: vertical;
        height: 6; 
        width: 100%;
        margin: 1 2 1 2;
    }

    #query-wrap {
        layout: horizontal;
        height: 5;
        width: 100%;
    }

    #query-spinner {
        width: 5;
        height: 1;
        margin-top: 2;
        margin-right: 1;
        display: none; 
    }


    #query-wrap.loading #query-spinner {
        display: block;
    }

    TextArea {
        height: 5; 
        width: 1fr; 
        box-sizing: border-box; 
        border: tall $accent;
    }

    LoadingIndicator {
        color: orange;
    }

    #counter-wrap {
        layout: horizontal;
        height: 1;
        width: 100%;
    }

    #token-counter {
        width: 1fr;
        text-align: left;
        padding-left: 2; 
        color: $text-muted;
        background: transparent;
        text-style: bold;
    }

    #cumulative-token-counter {
        width: 1fr;
        text-align: right;
        padding-right: 2; 
        color: $text-muted;
        background: transparent;
        text-style: bold;
    }
    """

    system_prompt_content: str = ""
 
    def __init__(self,**kwargs):
        super().__init__(**kwargs)


        self.llm = None
        self.base_llm = None
        self.memory_limit = 2048
        self.history = []  # Stores active conversational memory 
        
        # State tracking for multi-profile token management
        self.current_profile = None  
        self.profile_state = {}     
        self.profile_stats = {} 
        self._last_displayed_profile = None  # Prevents duplicate panel logs

        self.total_tokens_used = 0  
        self.cumulative_tokens_used = 0


   
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="chat-container", wrap=True, highlight=True, markup=True)
        yield Footer()

        # --- Input Section ---
        with Container(id="query-container"):

            # A horizontal row to place the loading spinner and the text box side by side
            with Horizontal(id="query-wrap"):

                # Loading animation
                yield LoadingIndicator(id="query-spinner")

                # A multiline text input/query area
                yield TextArea(placeholder="Type message... (Press ctrl+r to send)", id="chat-input")
                
            # Wrapped both counters (to show the token count and the cost) inside a dedicated horizontal row
            with Horizontal(id="counter-wrap"):
                yield Label("Total Tokens Per Query: 0", id="token-counter")
                yield Label("Total Cumulative Tokens: 0", id="cumulative-token-counter")

    
    # Textual lifecycle method that runs exactly once when the screen is first initialized and displayed
    def on_mount(self) -> None:
        self.title = "Mylo"

        #Reload active model
        self.reload_active_model()

        #focus the input box
        self.query_one("#chat-input").focus()


    #Update the token count and cost
    def update_token_ui(
        self, 
        query_tokens: int = 0, 
        session_tokens: int = 0,
        query_cost: float = 0.0,
        session_cost: float = 0.0 
    ) -> None:

        # 1. Convert integers to pretty compact strings ("2k", "2.5M", etc.)
        formatted_query = format_compact_tokens(query_tokens)
        formatted_session = format_compact_tokens(session_tokens)

        # display the token along with the cost (maximum of eight decimal places)
        query_display = f"Total Tokens Per Query: {formatted_query} ($ {query_cost:.8f})"
        session_display = f"Total Cumulative Tokens: {formatted_session} ($ {session_cost:.8f})"

        try:
            # Update the count
            self.query_one("#token-counter").update(query_display)
            self.query_one("#cumulative-token-counter").update(session_display)
        except Exception as e:
            if hasattr(self, "log"):
                self.log(f"Token update failed: {e}")

    

    def reload_active_model(self) -> None:
        """Loads or reloads the active model directly from the config env file."""
    
        # Clear out any lingering GitHub/GitLab tokens from the OS environment.
        # We do this before reloading because if a user DELETES a token,
        # it would otherwise stay stuck in memory. Popping them ensures a totally clean slate.
         
        os.environ.pop("GITHUB_TOKEN", None)
        os.environ.pop("GITLAB_TOKEN", None)
    
        # Re-read the config env. override=True forces it to update existing variables instead of skipping them
        load_dotenv(dotenv_path=ENV_FILE, override=True)

        # Grab a reference to the UI chat window so we can print status panels to it
        chat_log = self.query_one("#chat-container", RichLog)
        
        # Get the active profile name
        active = os.environ.get("ACTIVE_PROFILE")
        self.current_profile = active
    
        # 1. INITIALIZE: Set to 0 if not yet initialized
        if not hasattr(self, "total_tokens_used"):
            self.total_tokens_used = 0
        if not hasattr(self, "cumulative_tokens_used"):
            self.cumulative_tokens_used = 0

        # 2. UPDATE UI
        try:
            self.update_token_ui(
                query_tokens=self.total_tokens_used,
                session_tokens=self.cumulative_tokens_used
            )
        except Exception:
            pass

        # 3. RENDER CURRENT CONFIG PANEL
        if active:

            # Pull all the specific enviroment variables in the model profile
            provider = os.environ.get(f"PROFILE_{active}_PROVIDER")
            model_name = os.environ.get(f"PROFILE_{active}_MODEL_NAME")
            api_key = os.environ.get(f"PROFILE_{active}_API_KEY")
            
            # Safely convert the memory limit to an integer. Fallback to 2048 if there is value or type error
            try:
                self.memory_limit = int(os.environ.get(f"PROFILE_{active}_MEMORY_LIMIT", 2048))
            except (ValueError, TypeError):
                self.memory_limit = 2048

            github_token = os.environ.get("GITHUB_TOKEN")
            gitlab_token = os.environ.get("GITLAB_TOKEN")

            # Ensure all core settings exist before trying to render the current config panel
            if provider and model_name and api_key:
                base_llm = dynamic_llm(provider, model_name, api_key)
                if base_llm:

                    # Attach tool 
                    self.llm = base_llm.bind_tools(tools)
                
                    # Create boolean flags for the Config Panel
                    has_github = bool(github_token)
                    has_gitlab = bool(gitlab_token)
                
                    # Bundle the current config into a tuple
                    current_state = (
                        active, 
                        provider.lower(), 
                        model_name, 
                        self.memory_limit, 
                        has_github, 
                        has_gitlab
                    )
                    
                    #Compare the tuple against the last one displayed and only display if it is different
                    if not hasattr(self, "_last_displayed_state") or self._last_displayed_state != current_state:
                        self._last_displayed_state = current_state
                        self._last_displayed_profile = active 
                    
                        init_markdown = RichMarkdown(
                            f"* **Active Profile:** `{active}`\n"
                            f"* **Model Provider:** `{provider.title()}`\n"
                            f"* **Model:** `{model_name}`\n"
                            f"* **Memory Limit:** `{self.memory_limit}` tokens\n"
                            f"* **GitHub Token:** `{'Added' if github_token else 'Not Added'}`\n"
                            f"* **Gitlab Token:** `{'Added' if gitlab_token else 'Not Added'}`"
                        )
                    
                        # Wrapping the markdown in a coloured border panel
                        init_panel = Panel(
                            init_markdown,
                            title="[bold #00aaff]Current Configuration[/bold #00aaff]",
                            title_align="left",
                            border_style="#00f5ff",
                            padding=(1, 2),
                            expand=True
                        )

                        # print to the chat container 
                        chat_log.write(init_panel)
                        chat_log.write("")
                    return


        # --- Fallback Panel Logic ---
        self.llm = None
        self.base_llm = None
    
        # --- Create a dummy state if no active profile exist
        fallback_state = ("NONE_ACTIVATED", None, None, None, False, False)
    
        # check whether the tuple displayed is different from the previous one 
        if not hasattr(self, "_last_displayed_state") or self._last_displayed_state != fallback_state:
            self._last_displayed_state = fallback_state
        
            fallback_markdown = RichMarkdown(
                "* **Active Profile:** `None` \n"
                "* **Model:** `Not Added`\n"
                "* Press `Ctrl+l` to add model profiles and start querying Mylo"
            )

            #fallback panel displaying 
            fallback_panel = Panel(
                fallback_markdown,
                title="[bold #f76835]Current Configuration[/bold #f76835]",
                title_align="left",
                border_style="#f76835",
                padding=(1, 2),
                expand=True
            )
            chat_log.write(fallback_panel)
            chat_log.write("")



    def action_open_config(self) -> None:

        # Define a nested callback function. This function won't run immediately; 
        # instead, it gets saved and passed to the modal. It will execute ONLY when the modal is closed.
        def handle_config_update(result: dict | None) -> None:

            # Check if the modal returned a truthy value (a dictionary with action data).
            # If the user clicked "Cancel", the modal returned None, so we skip the reload.
            if result:
                self.reload_active_model()
            
            #Whether the user saves or cancel the config window, always the query box / chat input is focused
            self.query_one("#chat-input").focus()
        
        # Open the ConfigModal screen, pushing it on top of the current chat screen.
        # The 'callback' parameter tells Textual: "When this modal calls self.dismiss(), 
        # pass whatever was dismissed into the handle_config_update function."
        self.push_screen(ConfigModal(), callback=handle_config_update)


    # Action method that triggers when the user sends a message (ctrl + r)
    def action_submit_message(self) -> None:
        input_box = self.query_one("#chat-input", TextArea)

        # Extract the text in the input box and strip out any white space
        user_text = input_box.text.strip()

        # If user not typed anything, do nothing
        if not user_text:
            return

        # Render the user's message into the chat log using Markdown
        chat_log = self.query_one("#chat-container", RichLog)
        chat_log.write(RichMarkdown(f"**You:** {user_text}"))
        chat_log.write("") 
        
        # Clear the input box immediately so it's ready for the user's next input
        input_box.text = ""

        # If model is not activated then a helpful message is displayed in the chat container
        if self.llm is None:
            chat_log.write(RichMarkdown(" No model is added. Create a model profile via `Ctrl+l` and start querying Mylo"))
            chat_log.write("\n---")
            return
        
        #triggers the spinning animation 
        self.query_one("#query-wrap").add_class("loading")

        # Give the user input to the agent
        self.process_agent_response(user_text)

    

    def _hide_spinner(self) -> None:
        
        # Removes the 'loading' CSS class to hide the spinner once the AI finishes responding
        self.query_one("#query-wrap").remove_class("loading")

    
    # The @work(thread=True) decorator tells Textual to run this function in a background thread.
    # This prevents the UI from freezing while waiting for the agent to respond
    @work(thread=True)
    def process_agent_response(self, user_text: str) -> None:
        """
        Handles background message preparation, truncation compliance, 
        and an automated tool execution loop (Agent Loop).
        Tracks and records overall session token and costs dynamically per profile.
        """

        # Ensure cumulative counters exist across the app's lifespan
        if not hasattr(self, "cumulative_tokens_used"):
            self.cumulative_tokens_used = 0
        if not hasattr(self, "cumulative_cost_used"):
            self.cumulative_cost_used = 0.0

        # Reset the token counters specifc to this query
        self.total_tokens_used = 0
        self.total_cost_used = 0.0  

        # Ensure dict used to track the token usage per model profile exist
        if not hasattr(self, "profile_state"):
            self.profile_state = {}

        # 1. Fetch current profile identifier string
        model_name = getattr(self, "current_profile", "default")
        if not model_name:
            model_name = "default"

        # Initialize tracking maps for this specific profile if it's the first time the profile is used
        if model_name not in self.profile_state:
            self.profile_state[model_name] = {
                "total": 0, 
                "cumulative": 0, 
                "cumulative_cost": 0.0
            }

        # Load the saved cumulative tokens for this profile into working variables
        self.cumulative_tokens_used = self.profile_state[model_name]["cumulative"]
        self.cumulative_cost_used = self.profile_state[model_name].get("cumulative_cost", 0.0)
        self.total_tokens_used = 0 
        self.total_cost_used = 0.0

        # Push initial "0" values to the UI. 
        # MUST use call_from_thread because background threads cannot safely touch the UI directly.
        self.call_from_thread(
            self.update_token_ui, 
            self.total_tokens_used, 
            self.cumulative_tokens_used,
            self.total_cost_used,
            self.cumulative_cost_used
        )

        try:
            # Create a quick-lookup dictionary for our available tools
            tools_by_name = {}
            if tools:
                tools_by_name = {t.name: t for t in tools if hasattr(t, "name")}
            
            # Add the user's message to the conversation history
            self.history.append(HumanMessage(content=user_text))

            # ─── 2. THE AGENT LOOP ───────────────────────────────────────────
            # LLMs with tools require a loop: the LLM might decide to call a tool, 
            # and after seeing the tool's output, it might call ANOTHER tool, or finally respond.
            # We cap this at 5 loops to prevent infinite execution loops.
            max_loops = 5
            loop_count = 0

            while loop_count < max_loops:

                # Try to load a custom system prompt from the SYSTEM_PROMPT.md file, fallback to a default system prompt if the file dont exist
                try:
                    self.system_prompt_content = "Your name is Mylo, a helpful code analyses Github, Gitlab repositories." + PROMPT_FILE.read_text(encoding="utf-8")
                except Exception:
                    self.system_prompt_content = "Your name is Mylo, a helpful code analyses Github, Gitlab repositories."
                

                # Trim the previous history to fit within the memory limit
                payload = trim_history(self.history, self.memory_limit, self.system_prompt_content, self.base_llm)

                try:
                    # Send the payload to the llm
                    response = self.llm.invoke(payload)

                    input_tokens = 0
                    output_tokens = 0
                    actual_model_string = "unknown"  # Fallback tracker string

                    # Try to find the exact model name returned by the llm provider
                    if hasattr(response, "response_metadata") and response.response_metadata:
                        meta_dict = response.response_metadata
                        actual_model_string = meta_dict.get("model_name") or meta_dict.get("model") or actual_model_string

                    if (actual_model_string == "unknown" or not actual_model_string) and hasattr(self, "llm"):
                        actual_model_string = (
                            getattr(self.llm, "model_name", None) or 
                            getattr(self.llm, "model", None) or 
                            "unknown"
                        )
                    
                    # Different LLM providers (OpenAI vs Anthropic vs Groq format token usage in wildly different places. This block checks all known locations.
                    if hasattr(response, "usage_metadata") and response.usage_metadata:
                        meta = response.usage_metadata
                        input_tokens = meta.get("input_tokens") or meta.get("prompt_token_count") or meta.get("prompt_tokens") or 0
                        output_tokens = meta.get("output_tokens") or meta.get("candidates_token_count") or meta.get("completion_tokens") or 0
                    elif hasattr(response, "response_metadata") and response.response_metadata:
                        meta = response.response_metadata
                        usage_dict = meta.get("token_usage") or meta.get("usage") or meta or {}

                        if isinstance(usage_dict, dict):
                            input_tokens = usage_dict.get("prompt_tokens") or usage_dict.get("input_tokens") or usage_dict.get("prompt_eval_count") or 0
                            output_tokens = usage_dict.get("completion_tokens") or usage_dict.get("output_tokens") or usage_dict.get("eval_count") or 0
                    

                    step_tokens = input_tokens + output_tokens 

                    # Fallback if the specific in/out tokens were missing but a total was provided
                    if step_tokens == 0:
                        step_tokens = (
                            (hasattr(response, "usage_metadata") and response.usage_metadata.get("total_tokens")) or
                            (isinstance(usage_dict, dict) and usage_dict.get("total_tokens")) or 0
                        )

                    # Fetch price per token (Both input and output) dynamically using get_model_token_prices
                    input_rate, output_rate = get_model_token_prices(str(actual_model_string), provider_hint = str(model_name))
                    
                    
                    step_cost = (input_tokens * input_rate) + ((output_tokens) * output_rate)

                    # Save values directly back inside targeted active profile container storage
                    self.profile_state[model_name]["cumulative"] += step_tokens
                    self.profile_state[model_name]["total"] += step_tokens
                    self.profile_state[model_name]["cumulative_cost"] += step_cost
                    
                    # Update working variables for the UI
                    self.cumulative_tokens_used = self.profile_state[model_name]["cumulative"]
                    self.cumulative_cost_used = self.profile_state[model_name]["cumulative_cost"]
                
                    self.total_tokens_used += step_tokens
                    self.total_cost_used += step_cost

                    # Push the current token usage and cost to the ui
                    self.call_from_thread(
                        self.update_token_ui,
                        self.total_tokens_used,
                        self.cumulative_tokens_used,
                        self.total_cost_used,
                        self.cumulative_cost_used
                    )
                    
                    # Add the AI's response to history for context in the next loop
                    self.history.append(response)

                    # LLMs sometimes return content as a string, sometimes as a list of dicts (multimodal). Handle both.
                    raw_content = response.content
                    final_text = ""

                    if isinstance(raw_content, list):
                        text_parts = []
                        for part in raw_content:
                            if isinstance(part, str):
                                text_parts.append(part)
                            elif isinstance(part, dict) and "text" in part:
                                text_parts.append(part["text"])
                        final_text = "\n".join(text_parts).strip()
                    elif isinstance(raw_content, str):
                        final_text = raw_content.strip()

                    # If the AI generated actual text (not just a tool call), print it to the chat UI
                    if final_text:
                        self.call_from_thread(self.post_ai_message, final_text)

                    # --- Tool execution loop ---
                    if response.tool_calls:
                        for tool_call in response.tool_calls:
                            t_name = tool_call["name"]
                            t_args = tool_call["args"]
                            t_id = tool_call["id"]

                            # Display which tool that the agent is using
                            self.call_from_thread(
                                self.post_system_log,
                                f"*Mylo is executing tool:* `{t_name}`"
                            )

                            # Execute the actual Python function tied to this tool
                            if t_name in tools_by_name:
                                chosen_tool = tools_by_name[t_name]
                                if hasattr(chosen_tool, "invoke"):
                                    tool_output = chosen_tool.invoke(t_args)
                                else:
                                    tool_output = chosen_tool(**t_args) if isinstance(t_args, dict) else chosen_tool(t_args)
                            else:
                                tool_output = f"Error: Tool '{t_name}' is not available."

                            # Append the tool's output back into the history
                            # The next iteration of the while loop will send this back to the AI 
                            # so it can read the result and decide what to do next.
                            self.history.append(
                                ToolMessage(
                                    content=str(tool_output),
                                    tool_call_id=t_id,
                                    name=t_name
                                )
                            )

                        # Increment the loop counter and restart the while loop to send the tool results to the AI
                        loop_count += 1
                        continue
                    else:
                        
                        # No tool calls means the AI is done thinking and has given its final answer. Break the loop.
                        break

                # --- API error handeling ---
                except Exception as err:
                    err_msg = str(err).lower()
    
                    # Categorize the error based on common API error strings to give the user a clean, readable title
                    if any(kw in err_msg for kw in [
                        "context_length_exceeded", "maximum context length", 
                        "prompt is too long", "max tokens", "resource_exhausted"
                        ]):
                        error_title = "Context Window Reached"
        
                    elif any(kw in err_msg for kw in [
                        "api_key", "authentication", "unauthorized", "401", "403", "bad credentials"
                    ]):
                        
                        error_title = "API Authentication Failed"
        
                    elif "rate_limit" in err_msg or "429" in err_msg:
                        error_title = "Rate Limit Exceeded"
        
                    elif "not found" in err_msg or "404" in err_msg:
                        error_title = "Requested Resource Missing"
        
                    else:
                        error_title = "Runtime Execution Exception"

                    # Structure the api error into a clean JSON for better displaying
                    error_dict = {
                        "error_type": type(err).__name__,
                        "message":     str(err),
                        "args":       [str(a) for a in err.args] if err.args else [],
                        }
    
                    try:
                        # Prettify the JSON into a syntax-highlighted JSON block using Rich Syntax
                        error_json = json.dumps(error_dict, indent=4, ensure_ascii=False)
                        error_renderable = Syntax(error_json, "json", word_wrap=True, theme="monokai")
                    except (json.JSONDecodeError, TypeError):
                        error_renderable = Text(str(err), style="#ff5555")

                    chat_log = self.query_one("#chat-container", RichLog)
                    # Wrap the error in a red bordered panel
                    error_panel = Panel(
                        error_renderable,
                        title=f"[bold #ff5555]{error_title}[/bold #ff5555]",
                        title_align="left",
                        border_style="#ff5555",
                        padding=(1, 2),
                        expand=True
                        )
                    
                    # Write it to the ui
                    chat_log.write(error_panel)
                    chat_log.write("")

                    break # Stop the agent loop if an API error occures

            # The else block is triggered if the agent tried to use tools 5 times without giving a final answer
            else:
                self.call_from_thread(
                    self.post_system_log,
                    "**Warning:** Maximum execution loop threshold reached (5 steps)."
                )

        # --- Non API error handeling ---
        except Exception as global_err:
            try:
                error_dict_thread = {
                    "error_type": type(global_err).__name__,
                    "message":     str(global_err),
                    "args":       [str(a) for a in global_err.args] if global_err.args else [],
                }

                try:
                    # Prettify global dump and apply word-wrapped syntax highlighting
                    error_json_thread = json.dumps(error_dict_thread, indent=4, ensure_ascii=False)
                    error_renderable = Syntax(error_json_thread, "json", word_wrap=True, theme="monokai")
        
                except (json.JSONDecodeError, TypeError):
                    error_renderable = Text(str(global_err), style="#ff5555")

                chat_log = self.query_one("#chat-container", RichLog)

                error_panel = Panel(
                    error_renderable,
                    title="[bold #ff5555]Thread Loop Exception[/bold #ff5555]",
                    title_align="left",
                    border_style="#ff5555",
                    padding=(1, 2),
                    expand=True
                )

                chat_log.write(error_panel)
                chat_log.write("")
            except Exception:
                
                # Silently fail if even the error printing breaks
                pass

        # This guarantees that the loading spinner is always turned off when the background task finishes.    
        finally:
            self.call_from_thread(self._hide_spinner)


    def post_ai_message(self, output: str) -> None:
        """Pushes structured agent text outputs wrapped in a boxed panel layout."""

        # Grab the chat container widget
        chat_log = self.query_one("#chat-container", RichLog)


        # This converts "\n" text strings into actual python newline characters
        # so RichMarkdown can properly render the indentation and code blocks.
        clean_output = output.replace("\\n", "\n").strip()
        
        # Add a blank line for visual spacing before the message
        chat_log.write("") 


        # Convert the output to rendered Markdown (handles code blocks, bolding, lists, etc.) 
        markdown_content = RichMarkdown(clean_output)
        
        # Wrap the markdown inside a visually distinct, bordered panel
        boxed_message = Panel(
            markdown_content,
            title="[bold]Agent[/bold]",
            title_align="left",
            border_style="#ff7f50",  
            padding=(1, 2),
            expand=True          
        )
        
        # Print the panel into the chat container and add blank line for spacing
        chat_log.write(boxed_message)
        chat_log.write("")

    def post_system_log(self, log_text: str) -> None:
        """Pushes functional execution messages clean to the log frame."""

        #get the chat container widget
        chat_log = self.query_one("#chat-container", RichLog)
        chat_log.write("")

        # render the text as markdown 
        chat_log.write(RichMarkdown(log_text.strip()))
        chat_log.write("")
    

    def action_new_session(self) -> None:
        """Wipes active session memory arrays and resets token tracking variables globally."""
        
        # Empty the conversation history so the agent starts with a blank slate
        self.history = []
    
        # Reset global token variables
        self.total_tokens_used = 0
        self.cumulative_tokens_used = 0
    
        # Completely clear the dict based token traking for each model profile
        self.profile_state = {} 
    
        # Push the reset value to the UI    
        self.update_token_ui(0, 0)

        # Display that new session is started
        chat_log = self.query_one("#chat-container", RichLog)
        chat_log.write("\n────────────────────────────────────────────────────────────────")
        chat_log.write("Memory Cleared: Started New Session...")
        chat_log.write("────────────────────────────────────────────────────────────────\n")
    
        # Show system notification to confirm the new session to the user
        self.notify("Chat memory cleared!", title="New Session Started")

        # Focuses the chat input immediately
        self.query_one("#chat-input").focus()


    def action_api_token(self) -> None:
        """Pops up the direct github and gitlab token window and updates the UI on close."""

        # Nested callback function that runs after the token modal is closed
        def handle_token_update(result=None) -> None:
            
            if result is True:
                
                # By setting this to None, we intentionally break the caching 
                # mechanism inside reload_active_model(). This forces the config panel to re-print 
                # in the chat log, so the user can visually see the "GitHub Token: Added" status update.
                self._last_displayed_profile = None
                
                # 2. Re-read the env file and print the updated status panel
                self.reload_active_model()
            
            # Always return focus to input area, even on Cancel
            self.query_one("#chat-input").focus()

        # Open the Git token modal screen, passing in our callback function to handle the result
        self.push_screen(GitTokenModal(), callback=handle_token_update)


    def action_exit_app(self) -> None:
        """Triggers explicitly from Ctrl+t and gracefully closes the app."""
        self.exit()



def main():

    config_dir.mkdir(parents=True, exist_ok=True)
    
    if not PROMPT_FILE.exists():
        PROMPT_FILE.write_text(base_identity,encoding="utf-8")

    load_dotenv(dotenv_path=ENV_FILE, override=True)

    app = Mylo()
    app.run()


if __name__ == "__main__":
    main()
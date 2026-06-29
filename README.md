# Mylo

An agent build with Langchain and Textual for Github and Gitlab repo analysis. 

## About Mylo

The main goal of Mylo is not to be another coding agent, but an agent that help you to find and analyse code made by other developers in a more easier and simple way. This will allow you to find repos that may fix your current problem or may suitable for your current or future projects :)


## Installation

Mylo requires **python version >= 3.9**. Install it via pip as follows,

```bash
pip install mylo-agent
```

To run,

```bash
mylo
```

## Main Features

* Supports models from providers such as OpenAI, Anthropic, Google, and Groq via standard API keys. Support for additional providers is coming soon. (The api keys are stored in ~/mylo-config/.env)

* Allow you to rewrite and modify the system prompt of the agent (the system prompt can be found in ~/mylo-config/SYSTEM_PROMPT.md )

* Allow you to create model profiles and specify the memory limit for each profile.

* Each model profile shares the same memory (or context). Therefore you can switch between multiple model with different memory limit in the same session.

* The TUI displays the total token usage for each query and the total cumulative token (which is the total token usage by that model profile in one session). The token counting is seperate for each model profile that you use.

* Allows you to add github and gitlab tokens for more api requests by the agent. (The tokens are stored in ~/mylo-config/.env)



### Model Profile 

Model Profile is a compact way of storing the model name, provider name, api key and memory limit. You can add name for each model profile allowing you to quick load the model that you wanted. Model profiles allow you to change the memory limit of each model that you use, giving more control over your token usage

### what is memory limit?

memory limit is the the maximum number of tokens passed to the LLM as conversation history. The TUI utilizes LangChain's trim_messages to dynamically trim older history, ensuring the context never exceeds this limit. This trimming process never affects your system prompt.

## Agent Functionalities

- Mylo can find names of repos from github and gitlab based on your specific needs

- It can show the file structure of a repository

- It can fetch / retrieve any files from a repository such as readme.md (you can ask it to retrive a file by specifying the path of the file in the repo (folder/file format). if it is not inside any folder, then just specify only the filename ).

- Mylo can also analyse the fetched files

- Mylo can analyse a file more deeply by using the AST of the file (The agent currently supports 16 languages.)

- Mylo can also write the retrieved files into disk (should provide the path to which the file is to be saved).


***languages supported by the agent:*** Python, Javascript, Typescript, Lua, Rust, Kotlin, Swift, Dart, C, C++, C#, Java, Go, Scala, Ruby and Php


## Contributions

Contributions are highly welcome! Whether you are fixing a bug, optimizing performance, or adding a new tool to the agent, your help makes Mylo better.


For major architectural adjustments or feature requests, feel free to open an issue first to discuss what you would like to change.
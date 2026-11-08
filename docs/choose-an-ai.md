---
title: Choose an AI
description: Set up Ollama, Claude, or another service, and what each one means for your data.
---

The AI reads logs and container details to work out why something failed. It can look, but it can't change anything. A separate part of Digdown makes any changes, and only from a short list.

## Which one should I use?

| | Ollama | Claude | Another service | No AI |
|---|---|---|---|---|
| **Cost** | Free | Paid, per use | Depends on the service | Free |
| **Your logs go to** | Nowhere | Anthropic | That service | Nowhere |
| **Speed** | Minutes per failure | Should be faster | Depends | Instant |
| **Needs** | A capable computer | An API key | An API key | Nothing |
| **Tested with Digdown** | Yes, `qwen3:14b` | Not yet | Not yet | Yes |

**If your logs might hold personal data, use a local model.** Digdown masks secrets such as passwords before anything goes to the AI, but it doesn't remove personal data. [Read more](security.md).

The model must support **tool calling**. **Test connection** on the setup screen checks this for you.

## Ollama

Runs on your own computer. Nothing leaves it.

1. **Install Ollama** from [ollama.com](https://ollama.com), and make sure it's running.

2. **Download a model.** `qwen3:14b` is the one that's been tested, and it's about a 9 GB download. It worked on a Mac with 24 GB of memory.

   ```bash
   ollama pull qwen3:14b
   ```

3. **In Digdown's setup, choose Ollama.** Setup suggests the right address and lists your models. In the Docker install, the address is `http://host.docker.internal:11434/v1`.

4. **Click Test connection.** The first call can take a few minutes while the model loads.

**On Linux with the Docker install,** Ollama must also listen beyond its own computer, for example by setting `OLLAMA_HOST=0.0.0.0`. Use a firewall to keep other machines out.

## Claude

1. **Get an API key** from the [Anthropic Console](https://console.anthropic.com).
2. **In Digdown's setup, choose Claude,** and paste the key.
3. **Click Test connection.**

Set a spending limit in the Console, since each diagnosis makes several AI calls.

## Another service

This covers OpenAI, OpenRouter, LiteLLM, and anything else with an OpenAI-compatible address.

1. **Get an API key** from the service.
2. **In Digdown's setup, choose Another service.** Enter its address, usually ending in `/v1`, along with the key and a model name.
3. **Click Test connection.**

## Change it later

Go to **Settings → AI → Change setup**.

## How long will a diagnosis take?

It depends on your model and hardware. In our [tests](whats-been-tested.md), `qwen3:14b` on a 24 GB Mac took about 2 to 5 minutes per failure on test cases, and longer on real stacks. Digdown diagnoses one incident at a time, with broken containers first, so several failures together can take a while.

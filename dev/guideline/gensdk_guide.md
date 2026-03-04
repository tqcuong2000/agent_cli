# Google Gen SDK: Text Generation Guide

This guide provides an overview of using the Google Gen SDK for text generation with Gemini models.

**Source**: [Gemini API Docs - Text Generation](https://ai.google.dev/gemini-api/docs/text-generation)

---

## 1. SDK Setup & Initialization

Before you start, ensure you have your API key set up as an environment variable (e.g., `GEMINI_API_KEY`).

### Python
```python
from google import genai
client = genai.Client()
```

### JavaScript
```javascript
import { GoogleGenAI } from "@google/genai";
const ai = new GoogleGenAI({});
```

### Go
```go
import (
    "context"
    "google.golang.org/genai"
)
ctx := context.Background()
client, err := genai.NewClient(ctx, nil)
```

### Java
```java
import com.google.genai.Client;
Client client = new Client();
```

---

## 2. Basic Text Generation

Generate a simple response from a text prompt.

### Python
```python
response = client.models.generate_content(
    model="gemini-2.0-flash",
    contents="How does AI work?"
)
print(response.text)
```

### JavaScript
```javascript
const response = await ai.models.generateContent({
    model: "gemini-2.0-flash",
    contents: "How does AI work?",
});
console.log(response.text);
```

### Go
```go
result, _ := client.Models.GenerateContent(
    ctx, "gemini-2.0-flash", genai.Text("Explain how AI works in a few words"), nil,
)
fmt.Println(result.Text())
```

### Java
```java
GenerateContentResponse response = client.models.generateContent("gemini-2.0-flash", "How does AI work?", null);
System.out.println(response.text());
```

---

## 3. Thinking with Gemini (Reasoning)

Gemini models can be configured to "think" or reason before responding.

### Python
```python
from google.genai import types
response = client.models.generate_content(
    model="gemini-2.0-flash-thinking-preview",
    contents="How does AI work?",
    config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(include_thoughts=True)
    ),
)
print(response.text)
```

### JavaScript
```javascript
const response = await ai.models.generateContent({
    model: "gemini-2.0-flash-thinking-preview",
    contents: "How does AI work?",
    config: {
        thinkingConfig: { includeThoughts: true },
    }
});
console.log(response.text);
```

---

## 4. System Instructions & Configuration

Guide the model's behavior using system instructions.

### Python
```python
from google.genai import types
response = client.models.generate_content(
    model="gemini-2.0-flash",
    config=types.GenerateContentConfig(
        system_instruction="You are a helpful assistant that speaks like a pirate."),
    contents="Hello there"
)
print(response.text)
```

### JavaScript
```javascript
const response = await ai.models.generateContent({
    model: "gemini-2.0-flash",
    contents: "Hello there",
    config: {
        systemInstruction: "You are a helpful assistant that speaks like a pirate.",
    },
});
console.log(response.text);
```

---

## 5. Multimodal Inputs (Text + Image)

Gemini supports combining text with media files.

### Python
```python
from PIL import Image
image = Image.open("image.png")
response = client.models.generate_content(
    model="gemini-2.0-flash",
    contents=[image, "Tell me about this image"]
)
print(response.text)
```

### JavaScript
```javascript
const image = await ai.files.upload({ file: "image.png" });
const response = await ai.models.generateContent({
    model: "gemini-2.0-flash",
    contents: [
        createUserContent([
            "Tell me about this image",
            createPartFromUri(image.uri, image.mimeType),
        ]),
    ],
});
console.log(response.text);
```

---

## 6. Streaming Responses

Receive responses incrementally for fluid interactions.

### Python
```python
response = client.models.generate_content_stream(
    model="gemini-2.0-flash",
    contents=["Explain how AI works"]
)
for chunk in response:
    print(chunk.text, end="")
```

### JavaScript
```javascript
const response = await ai.models.generateContentStream({
    model: "gemini-2.0-flash",
    contents: "Explain how AI works",
});
for await (const chunk of response) {
    process.stdout.write(chunk.text);
}
```

---

## 7. Multi-turn Conversations (Chat)

Manage conversation history easily.

### Python
```python
chat = client.chats.create(model="gemini-2.0-flash")
response = chat.send_message("I have 2 dogs.")
print(response.text)
response = chat.send_message("How many paws are in my house?")
print(response.text)
```

### JavaScript
```javascript
const chat = ai.chats.create({ model: "gemini-2.0-flash" });
const response1 = await chat.sendMessage({ message: "I have 2 dogs." });
console.log(response1.text);
const response2 = await chat.sendMessage({ message: "How many paws?" });
console.log(response2.text);
```

---

## 8. REST API Usage

### Generate Content
```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent" \
    -H "x-goog-api-key: $GEMINI_API_KEY" \
    -H 'Content-Type: application/json' \
    -X POST \
    -d '{
      "contents": [{
        "parts": [{"text": "How does AI work?"}]
      }]
    }'
```

---

> [!TIP]
> Always check the [official documentation](https://ai.google.dev/gemini-api/docs/text-generation) for the latest model names and parameter updates.

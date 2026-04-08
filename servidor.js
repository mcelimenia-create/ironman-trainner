require("dotenv").config();
const express = require("express");
const axios = require("axios");

const app = express();
app.use(express.json());

const users = {}; // memoria simple (luego la cambiamos por DB)

app.post("/webhook", async (req, res) => {
  try {
    const message = req.body.message?.text;
    const chatId = req.body.message?.chat?.id;

    if (!message) return res.sendStatus(200);

    // 🧠 memoria del usuario
    if (!users[chatId]) {
      users[chatId] = {
        objetivo: "Ironman en 6 meses",
        fatiga: 5,
        historial: []
      };
    }

    users[chatId].historial.push(message);

    // 🧠 prompt inteligente
    const systemPrompt = `
Eres un entrenador profesional de Ironman.

Datos del atleta:
- Objetivo: ${users[chatId].objetivo}
- Fatiga: ${users[chatId].fatiga}/10
- Últimos mensajes: ${users[chatId].historial.slice(-3).join(", ")}

Responde con:
- Entrenamiento claro
- Consejos específicos
- Motivación breve
`;

    // 🤖 llamada a Claude
    const response = await axios.post(
      "https://api.anthropic.com/v1/messages",
      {
        model: "claude-3-haiku-20240307",
        max_tokens: 300,
        messages: [
          { role: "user", content: systemPrompt + "\n\nUsuario: " + message }
        ]
      },
      {
        headers: {
          "x-api-key": process.env.CLAUDE_API_KEY,
          "anthropic-version": "2023-06-01",
          "content-type": "application/json"
        }
      }
    );

    const reply = response.data.content[0].text;

    // 📤 responder a Telegram
    await axios.post(
      `https://api.telegram.org/bot${process.env.TELEGRAM_TOKEN}/sendMessage`,
      {
        chat_id: chatId,
        text: reply
      }
    );

    res.sendStatus(200);
  } catch (err) {
    console.error(err);
    res.sendStatus(500);
  }
});

app.listen(3000, () => console.log("🚀 Bot listo en puerto 3000"));
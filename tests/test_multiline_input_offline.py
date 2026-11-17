import unittest
from unittest.mock import patch, MagicMock
from io import StringIO

from src.ui.cli import leer_texto_multilinea_cli, limpiar_buffer_teclado


class TestMultilineInputOffline(unittest.TestCase):

    def test_whatsapp_message_with_internal_blank_lines(self):
        whatsapp_raw = """Hola buenas tardes, disculpe la hora jaja. Le escribo porque necesito una cotización para un proyecto de la universidad.

Necesito:

- 5 Arduino Uno R3

- 2 ESP32 NodeMCU, uno con antena externa y otro sin antena

- 20 resistencias, 10 de 220 ohm y 10 de 10k ohm

- 15 LEDs, 5 rojos, 5 verdes y 5 azules de 5mm

- 3 sensores ultrasónicos HC-SR04

- 2 módulos bluetooth HC-05

- 4 servomotores SG90

- 1 pantalla OLED 0.96 I2C

- 2 fuentes de 12V 2A

- 1 rollo de cable dupont macho-hembra

- 6 potenciómetros de 10k

- 3 relés de 5V de un solo canal

- 1 protoboard grande de 830 puntos

- 2 baterías de 9V con conector

- 1 caja organizadora para guardar todo

Ah, también si tienen disponible me gustaría que me agregaran un cautín de 60W porque el mío ya no sirve.

Otra cosa, adicionalmente necesito un multímetro digital básico si no se pasa mucho del presupuesto.

Y bueno también quería preguntar, ¿cuánto tardarían en entregar? Es que lo necesito para el jueves si es posible. De ante mano muchas gracias, saludos!
FIN
"""
        with patch("sys.stdin", StringIO(whatsapp_raw)):
            captured = leer_texto_multilinea_cli()

        self.assertIsNotNone(captured)
        self.assertIn("Hola buenas tardes", captured)
        self.assertIn("Necesito:", captured)
        self.assertIn("- 5 Arduino Uno R3", captured)
        self.assertIn("cautín de 60W", captured)
        self.assertIn("¿cuánto tardarían en entregar?", captured)
        self.assertNotIn("FIN", captured.splitlines())

    def test_phrases_containing_fin_do_not_terminate(self):
        text = """- 2 microswitch fin de carrera
- 1 cautín con punta fina
Lo necesito a fin de mes por favor
FIN
"""
        with patch("sys.stdin", StringIO(text)):
            captured = leer_texto_multilinea_cli()

        self.assertIsNotNone(captured)
        lines = captured.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "- 2 microswitch fin de carrera")
        self.assertEqual(lines[1], "- 1 cautín con punta fina")
        self.assertEqual(lines[2], "Lo necesito a fin de mes por favor")

    def test_single_line_input(self):
        text = "3x Arduino Uno, 2x ESP32\nFIN\n"
        with patch("sys.stdin", StringIO(text)):
            captured = leer_texto_multilinea_cli()

        self.assertEqual(captured, "3x Arduino Uno, 2x ESP32")

    def test_eof_termination(self):
        # Simula EOF (Ctrl+D) sin escribir FIN
        text = "- 5 Arduino Uno\n- 2 ESP32"
        with patch("sys.stdin", StringIO(text)):
            captured = leer_texto_multilinea_cli()

        self.assertIsNotNone(captured)
        self.assertEqual(captured.splitlines(), ["- 5 Arduino Uno", "- 2 ESP32"])

    def test_cancel_keyword(self):
        text = "- 5 Arduino Uno\nCANCELAR\n"
        with patch("sys.stdin", StringIO(text)):
            captured = leer_texto_multilinea_cli()

        self.assertIsNone(captured)

    def test_keyboard_interrupt(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            captured = leer_texto_multilinea_cli()

        self.assertIsNone(captured)

    def test_empty_input_then_fin(self):
        text = "\n\n   \nFIN\n"
        with patch("sys.stdin", StringIO(text)):
            captured = leer_texto_multilinea_cli()

        self.assertIsNone(captured)

    def test_limpiar_buffer_teclado_safe(self):
        with patch("sys.stdin", StringIO("resto_no_leido\n")):
            limpiar_buffer_teclado()

        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        with patch("sys.stdin", mock_stdin):
            limpiar_buffer_teclado()

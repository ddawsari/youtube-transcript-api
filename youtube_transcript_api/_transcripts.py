import sys

# This can only be tested by using different python versions, therefore it is not covered by coverage.py
if sys.version_info.major == 2: # pragma: no cover
    reload(sys)
    sys.setdefaultencoding('utf-8')

import json

from xml.etree import ElementTree

import re

from requests import HTTPError

from ._html_unescaping import unescape
from ._errors import (
    VideoUnavailable,
    TooManyRequests,
    YouTubeRequestFailed,
    NoTranscriptFound,
    TranscriptsDisabled,
    NotTranslatable,
    TranslationLanguageNotAvailable,
    NoTranscriptAvailable,
    FailedToCreateConsentCookie,
)
from ._settings import WATCH_URL


def _raise_http_errors(response, video_id):
    try:
        response.raise_for_status()
        return response
    except HTTPError as error:
        raise YouTubeRequestFailed(error, video_id)


class TranscriptListFetcher(object):
    def __init__(self, http_client):
        self._http_client = http_client

    def fetch(self, video_id):
        return TranscriptList.build(
            self._http_client,
            video_id,
            *self._extract_captions_json(self._fetch_video_html(video_id), video_id)
            #self._extract_captions_json(self._fetch_video_html(video_id), video_id)
        )

    def _extract_captions_json(self, html, video_id):
        splitted_html = html.split('"captions":')

        if len(splitted_html) <= 1:
            if 'class="g-recaptcha"' in html:
                raise TooManyRequests(video_id)
            if '"playabilityStatus":' not in html:
                raise VideoUnavailable(video_id)

            raise TranscriptsDisabled(video_id)

        captions_json = json.loads(
            splitted_html[1].split(',"videoDetails')[0].replace('\n', '')
        ).get('playerCaptionsTracklistRenderer')
        if captions_json is None:
            raise TranscriptsDisabled(video_id)

        if 'captionTracks' not in captions_json:
            raise NoTranscriptAvailable(video_id)
        # adding video details
        # TODO:// make sure all cases word description usually has a lot of characters that could be invalid (fixed :D)
        # video_info = json.loads(splitted_html[1].split(',"annotations')[0].split(',"videoDetails":')[1].replace('\n', '').replace("”\"","”"))
        #pre desc
        pre_desc = json.loads(splitted_html[1].split(',"annotations')[0].split(',"videoDetails":')[1].split(',"shortDescription":')[0].replace('\n', '').replace("”\"","”")+"}")
        #desc
        video_desc = {'shortDescription': splitted_html[1].split(',"annotations')[0].split(',"videoDetails":')[1].split(',"shortDescription":')[1].split(',"isCrawlable":')[0].replace('\n', '').replace("”\"","”")}
        #post_desc
        post_desc = json.loads('{"isCrawlable":'+splitted_html[1].split(',"annotations')[0].split(',"videoDetails":')[1].split(',"shortDescription":')[1].split(',"isCrawlable":')[1].replace('\n', '').replace("”\"","”"))
        pre_desc.update(video_desc)
        pre_desc.update(post_desc)
        
        video_info = pre_desc
        return captions_json, video_info
        #return captions_json

    def _create_consent_cookie(self, html, video_id):
        match = re.search('name="v" value="(.*?)"', html)
        if match is None:
            raise FailedToCreateConsentCookie(video_id)
        self._http_client.cookies.set('CONSENT', 'YES+' + match.group(1), domain='.youtube.com')

    def _fetch_video_html(self, video_id):
        html = self._fetch_html(video_id)
        if 'action="https://consent.youtube.com/s"' in html:
            self._create_consent_cookie(html, video_id)
            html = self._fetch_html(video_id)
            if 'action="https://consent.youtube.com/s"' in html:
                raise FailedToCreateConsentCookie(video_id)
        return html

    def _fetch_html(self, video_id):
        response = self._http_client.get(WATCH_URL.format(video_id=video_id))
        return _raise_http_errors(response, video_id).text.replace(
            '\\u0026', '&'
        ).replace(
            '\\', ''
        )


class TranscriptList(object):
    """
    This object represents a list of transcripts. It can be iterated over to list all transcripts which are available
    for a given YouTube video. Also it provides functionality to search for a transcript in a given language.
    """
    def __init__(self, video_id, manually_created_transcripts, generated_transcripts, translation_languages, details_json):
    #def __init__(self, video_id, manually_created_transcripts, generated_transcripts, translation_languages):
        """
        The constructor is only for internal use. Use the static build method instead.

        :param video_id: the id of the video this TranscriptList is for
        :type video_id: str
        :param manually_created_transcripts: dict mapping language codes to the manually created transcripts
        :type manually_created_transcripts: dict[str, Transcript]
        :param generated_transcripts: dict mapping language codes to the generated transcripts
        :type generated_transcripts: dict[str, Transcript]
        :param translation_languages: list of languages which can be used for translatable languages
        :type translation_languages: list[dict[str, str]]
        """
        self.video_id = video_id
        self._manually_created_transcripts = manually_created_transcripts
        self._generated_transcripts = generated_transcripts
        self._translation_languages = translation_languages
        self.details_json = details_json
    @staticmethod
    def build(http_client, video_id, captions_json, details_json):
    #def build(http_client, video_id, captions_json):
        """
        Factory method for TranscriptList.

        :param http_client: http client which is used to make the transcript retrieving http calls
        :type http_client: requests.Session
        :param video_id: the id of the video this TranscriptList is for
        :type video_id: str
        :param captions_json: the JSON parsed from the YouTube pages static HTML
        :type captions_json: dict
        :return: the created TranscriptList
        :rtype TranscriptList:
        """
        translation_languages = [
            {
                'language': translation_language['languageName']['simpleText'],
                'language_code': translation_language['languageCode'],
            } for translation_language in captions_json['translationLanguages']
        ]

        manually_created_transcripts = {}
        generated_transcripts = {}

        for caption in captions_json['captionTracks']:
            if caption.get('kind', '') == 'asr':
                transcript_dict = generated_transcripts
            else:
                transcript_dict = manually_created_transcripts

            transcript_dict[caption['languageCode']] = Transcript(
                http_client,
                video_id,
                caption['baseUrl'],
                caption['name']['simpleText'],
                caption['languageCode'],
                caption.get('kind', '') == 'asr',
                translation_languages if caption.get('isTranslatable', False) else [],
                details_json
            )

        return TranscriptList(
            video_id,
            manually_created_transcripts,
            generated_transcripts,
            translation_languages,
            details_json
        )

    def __iter__(self):
        return iter(list(self._manually_created_transcripts.values()) + list(self._generated_transcripts.values()))

    def find_transcript(self, language_codes):
        """
        Finds a transcript for a given language code. Manually created transcripts are returned first and only if none
        are found, generated transcripts are used. If you only want generated transcripts use
        find_manually_created_transcript instead.

        :param language_codes: A list of language codes in a descending priority. For example, if this is set to
        ['de', 'en'] it will first try to fetch the german transcript (de) and then fetch the english transcript (en) if
        it fails to do so.
        :type languages: list[str]
        :return: the found Transcript
        :rtype Transcript:
        :raises: NoTranscriptFound
        """
        return self._find_transcript(language_codes, [self._manually_created_transcripts, self._generated_transcripts])

    def find_generated_transcript(self, language_codes):
        """
        Finds a automatically generated transcript for a given language code.

        :param language_codes: A list of language codes in a descending priority. For example, if this is set to
        ['de', 'en'] it will first try to fetch the german transcript (de) and then fetch the english transcript (en) if
        it fails to do so.
        :type languages: list[str]
        :return: the found Transcript
        :rtype Transcript:
        :raises: NoTranscriptFound
        """
        return self._find_transcript(language_codes, [self._generated_transcripts,])

    def find_manually_created_transcript(self, language_codes):
        """
        Finds a manually created transcript for a given language code.

        :param language_codes: A list of language codes in a descending priority. For example, if this is set to
        ['de', 'en'] it will first try to fetch the german transcript (de) and then fetch the english transcript (en) if
        it fails to do so.
        :type languages: list[str]
        :return: the found Transcript
        :rtype Transcript:
        :raises: NoTranscriptFound
        """
        return self._find_transcript(language_codes, [self._manually_created_transcripts,])

    def _find_transcript(self, language_codes, transcript_dicts):
        for language_code in language_codes:
            for transcript_dict in transcript_dicts:
                if language_code in transcript_dict:
                    return transcript_dict[language_code]

        raise NoTranscriptFound(
            self.video_id,
            language_codes,
            self
        )

    def __str__(self):
        return (
            'For this video ({video_id}) transcripts are available in the following languages:\n\n'
            '(MANUALLY CREATED)\n'
            '{available_manually_created_transcript_languages}\n\n'
            '(GENERATED)\n'
            '{available_generated_transcripts}\n\n'
            '(TRANSLATION LANGUAGES)\n'
            '{available_translation_languages}'
        ).format(
            video_id=self.video_id,
            available_manually_created_transcript_languages=self._get_language_description(
                str(transcript) for transcript in self._manually_created_transcripts.values()
            ),
            available_generated_transcripts=self._get_language_description(
                str(transcript) for transcript in self._generated_transcripts.values()
            ),
            available_translation_languages=self._get_language_description(
                '{language_code} ("{language}")'.format(
                    language=translation_language['language'],
                    language_code=translation_language['language_code'],
                ) for translation_language in self._translation_languages
            ),
            details_json=self.details_json,

        )

    def _get_language_description(self, transcript_strings):
        description = '\n'.join(' - {transcript}'.format(transcript=transcript) for transcript in transcript_strings)
        return description if description else 'None'


class Transcript(object):
    def __init__(self, http_client, video_id, url, language, language_code, is_generated, translation_languages, details_json):
    #def __init__(self, http_client, video_id, url, language, language_code, is_generated, translation_languages):
        """
        You probably don't want to initialize this directly. Usually you'll access Transcript objects using a
        TranscriptList.

        :param http_client: http client which is used to make the transcript retrieving http calls
        :type http_client: requests.Session
        :param video_id: the id of the video this TranscriptList is for
        :type video_id: str
        :param url: the url which needs to be called to fetch the transcript
        :param language: the name of the language this transcript uses
        :param language_code:
        :param is_generated:
        :param translation_languages:
        """
        self._http_client = http_client
        self.video_id = video_id
        self._url = url
        self.language = language
        self.language_code = language_code
        self.is_generated = is_generated
        self.translation_languages = translation_languages
        self._translation_languages_dict = {
            translation_language['language_code']: translation_language['language']
            for translation_language in translation_languages
        }
        self.details_json =details_json

    def fetch(self):
        """
        Loads the actual transcript data.

        :return: a list of dictionaries containing the 'text', 'start' and 'duration' keys
        :rtype [{'text': str, 'start': float, 'end': float}]:
        """
        response = self._http_client.get(self._url)
        return {"details":self.details_json,"transcript":_TranscriptParser().parse(
            _raise_http_errors(response, self.video_id).text,
        )}
        #return _TranscriptParser().parse(
        #    _raise_http_errors(response, self.video_id).text,
        #)

    def __str__(self):
        return '{language_code} ("{language}"){translation_description}'.format(
            language=self.language,
            language_code=self.language_code,
            translation_description='[TRANSLATABLE]' if self.is_translatable else ''
        )

    @property
    def is_translatable(self):
        return len(self.translation_languages) > 0

    def translate(self, language_code):
        if not self.is_translatable:
            raise NotTranslatable(self.video_id)

        if language_code not in self._translation_languages_dict:
            raise TranslationLanguageNotAvailable(self.video_id)

        return Transcript(
            self._http_client,
            self.video_id,
            '{url}&tlang={language_code}'.format(url=self._url, language_code=language_code),
            self._translation_languages_dict[language_code],
            language_code,
            True,
            [],
            self.details_json
        )


class _TranscriptParser(object):
    HTML_TAG_REGEX = re.compile(r'<[^>]*>', re.IGNORECASE)

    def parse(self, plain_data):
        return [
            {
                'text': re.sub(self.HTML_TAG_REGEX, '', unescape(xml_element.text)),
                'start': float(xml_element.attrib['start']),
                'duration': float(xml_element.attrib.get('dur', '0.0')),
            }
            for xml_element in ElementTree.fromstring(plain_data)
            if xml_element.text is not None
        ]

SETUP INSTRUCTIONS FOR A GNOME SHELL SEARCH PROVIDER BACKED BY CLAUDE

OVERVIEW

This implementation uses a user-session D-Bus service that implements org.gnome.Shell.SearchProvider2.

The provider design is:

- GetInitialResultSet and GetSubsearchResultSet perform the Claude request
- They only return a result ID after the Claude answer is ready
- GetResultMetas only reads cached metadata and returns it immediately
- ActivateResult and LaunchSearch do nothing

This avoids placeholder rows and avoids trying to update an already-rendered row later.

INSTALL PATHS

Install the files here:

```sh
/usr/local/bin/claude-search-provider-gjs
/usr/local/share/dbus-1/services/com.example.ClaudeSearchProvider.service
/usr/local/share/applications/com.example.ClaudeSearchProvider.desktop
/usr/local/share/gnome-shell/search-providers/com.example.ClaudeSearchProvider.search-provider.ini
```

Set the script executable:

```sh
chmod 0755 /usr/local/bin/claude-search-provider-gjs
```

Make sure ANTHROPIC_API_KEY is available to the user session that will own the D-Bus service.

SEARCH PROVIDER REGISTRATION FILE

Path:

```sh
/usr/local/share/gnome-shell/search-providers/com.example.ClaudeSearchProvider.search-provider.ini
```

Contents:

```sh
[Shell Search Provider]
DesktopId=com.example.ClaudeSearchProvider.desktop
BusName=com.example.ClaudeSearchProvider
ObjectPath=/com/example/ClaudeSearchProvider
Version=2
```

DESKTOP FILE

Path:

```sh
/usr/local/share/applications/com.example.ClaudeSearchProvider.desktop
```

Contents:

```sh
[Desktop Entry]
Type=Application
Name=Claude Search Provider
Comment=GNOME Shell search provider backed by Claude Sonnet
Exec=/usr/bin/gjs -m /usr/local/bin/claude-search-provider-gjs
Icon=system-search-symbolic
DBusActivatable=false
```

D-BUS SERVICE FILE

Path:

```sh
/usr/local/share/dbus-1/services/com.example.ClaudeSearchProvider.service
```

Contents:

```sh
[D-BUS Service]
Name=com.example.ClaudeSearchProvider
Exec=/usr/bin/gjs -m /usr/local/bin/claude-search-provider-gjs
```

FULL GJS SCRIPT

Path:
/usr/local/bin/claude-search-provider-gjs

Contents:

#!/usr/bin/gjs -m

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Soup from 'gi://Soup?version=3.0';

Gio.\_promisify(Soup.Session.prototype, 'send_and_read_async', 'send_and_read_finish');

const BUS_NAME = 'com.example.ClaudeSearchProvider';
const OBJECT_PATH = '/com/example/ClaudeSearchProvider';

const IFACE_XML = `<node>
  <interface name="org.gnome.Shell.SearchProvider2">
    <method name="GetInitialResultSet">
      <arg type="as" name="terms" direction="in" />
      <arg type="as" name="results" direction="out" />
    </method>
    <method name="GetSubsearchResultSet">
      <arg type="as" name="previous_results" direction="in" />
      <arg type="as" name="terms" direction="in" />
      <arg type="as" name="results" direction="out" />
    </method>
    <method name="GetResultMetas">
      <arg type="as" name="identifiers" direction="in" />
      <arg type="aa{sv}" name="metas" direction="out" />
    </method>
    <method name="ActivateResult">
      <arg type="s" name="identifier" direction="in" />
      <arg type="as" name="terms" direction="in" />
      <arg type="u" name="timestamp" direction="in" />
    </method>
    <method name="LaunchSearch">
      <arg type="as" name="terms" direction="in" />
      <arg type="u" name="timestamp" direction="in" />
    </method>
  </interface>
</node>`;

function
truncate_one_line(
text,
limit
) {
let normalized;

        normalized = String(text).replace(/\s+/g, ' ').trim();

        if (normalized.length <= limit)
                return normalized;

        return normalized.slice(0, limit - 1) + '…';

}

function
parse_claude_display_text(
text,
fallback_query
) {
let lines;
let title;
let description;
let line;

        lines = String(text)
                .split('\n')
                .map(line_text => line_text.trim())
                .filter(line_text => line_text.length > 0);

        title = '';
        description = '';

        for (line of lines) {
                if (line.startsWith('TITLE:')) {
                        title = line.slice('TITLE:'.length).trim();
                        continue;
                }

                if (line.startsWith('DESCRIPTION:')) {
                        description = line.slice('DESCRIPTION:'.length).trim();
                        continue;
                }
        }

        if (title.length === 0 && lines.length > 0)
                title = lines[0];

        if (description.length === 0 && lines.length > 1)
                description = lines.slice(1).join(' ');

        if (title.length === 0)
                title = `Claude: ${fallback_query}`;

        if (description.length === 0)
                description = 'No summary returned';

        return {
                title: truncate_one_line(title, 72),
                description: truncate_one_line(description, 160),
        };

}

function
build_claude_prompt(
query
) {
return [
'You are formatting an answer for a GNOME Shell search result.',
'Return exactly two lines, and nothing else.',
'Line 1 must start with: TITLE:',
'Line 2 must start with: DESCRIPTION:',
'Constraints:',
'- TITLE must be short, concrete, and useful as a result title.',
'- DESCRIPTION must be a single plain-text sentence or sentence fragment.',
'- Do not use markdown.',
'- Do not use bullets.',
'- Do not include extra commentary.',
'- Do not include line breaks inside either field.',
'- Keep TITLE under 72 characters.',
'- Keep DESCRIPTION under 160 characters.',
'- Prefer dense, glanceable wording that reads well in a GNOME Shell search result.',
'',
`User query: ${query}`,
].join('\n');
}

async function
call_claude(
query,
cancellable
) {
let api_key;
let model;
let session;
let payload;
let message;
let body;
let bytes;
let response_bytes;
let status;
let response_text;
let response;
let parts;
let text;
let prompt;

        api_key = GLib.getenv('ANTHROPIC_API_KEY');
        if (!api_key)
                throw new Error('ANTHROPIC_API_KEY is not set');

        model = GLib.getenv('CLAUDE_MODEL') || 'claude-sonnet-4-6';
        prompt = build_claude_prompt(query);

        session = new Soup.Session();

        payload = {
                model,
                max_tokens: 120,
                messages: [
                        {
                                role: 'user',
                                content: prompt,
                        },
                ],
        };

        message = Soup.Message.new('POST', 'https://api.anthropic.com/v1/messages');
        message.request_headers.append('x-api-key', api_key);
        message.request_headers.append('anthropic-version', '2023-06-01');
        message.request_headers.append('content-type', 'application/json');

        body = JSON.stringify(payload);
        bytes = new GLib.Bytes(body);
        message.set_request_body_from_bytes('application/json', bytes);

        response_bytes = await session.send_and_read_async(
                message,
                GLib.PRIORITY_DEFAULT,
                cancellable
        );

        status = message.get_status();
        response_text = new TextDecoder().decode(response_bytes.toArray());

        if (status < 200 || status >= 300)
                throw new Error(`Claude API HTTP ${status}: ${response_text}`);

        response = JSON.parse(response_text);
        parts = response.content || [];

        text = parts
                .filter(part => part.type === 'text')
                .map(part => part.text || '')
                .join('')
                .trim();

        if (!text)
                throw new Error('Claude returned no text');

        return parse_claude_display_text(text, query);

}

class ClaudeSearchProvider {
constructor() {
this.\_node_info = Gio.DBusNodeInfo.new_for_xml(IFACE_XML);
this.\_iface_info = this.\_node_info.interfaces[0];
this.\_cache = new Map();
this.\_search_serial = 0;
this.\_current_cancellable = null;
}

        export(connection) {
                this._registration_id = connection.register_object(
                        OBJECT_PATH,
                        this._iface_info,
                        this._handle_method_call.bind(this),
                        null,
                        null
                );
        }

        _extract_prompt_from_terms(terms) {
                let query;

                query = terms.join(' ').trim();

                if (query.length < 4)
                        return null;

                return query;
        }

        _make_id(prompt) {
                return `claude:${encodeURIComponent(prompt)}`;
        }

        _prompt_from_id(id) {
                if (!id.startsWith('claude:'))
                        return null;

                return decodeURIComponent(id.slice('claude:'.length));
        }

        _trim_cache() {
                let limit;
                let first_key;

                limit = 50;

                while (this._cache.size > limit) {
                        first_key = this._cache.keys().next().value;
                        this._cache.delete(first_key);
                }
        }

        _meta_from_display(
                id,
                display
        ) {
                return {
                        id: new GLib.Variant('s', id),
                        name: new GLib.Variant('s', display.title),
                        description: new GLib.Variant('s', display.description),
                        gicon: new GLib.Variant('s', 'system-search-symbolic'),
                };
        }

        _fallback_meta(
                id,
                prompt,
                description
        ) {
                let title;

                if (prompt)
                        title = truncate_one_line(`Claude: ${prompt}`, 72);
                else
                        title = 'Claude';

                return {
                        id: new GLib.Variant('s', id),
                        name: new GLib.Variant('s', title),
                        description: new GLib.Variant('s', truncate_one_line(description, 160)),
                        gicon: new GLib.Variant('s', 'system-search-symbolic'),
                };
        }

        _cancel_previous_search() {
                if (this._current_cancellable) {
                        this._current_cancellable.cancel();
                        this._current_cancellable = null;
                }
        }

        async _search_prompt(
                prompt,
                invocation
        ) {
                let serial;
                let cancellable;
                let display;
                let result_ids;
                let error_message;

                if (this._cache.has(prompt)) {
                        result_ids = [this._make_id(prompt)];
                        invocation.return_value(
                                new GLib.Variant('(as)', [result_ids])
                        );
                        return;
                }

                this._cancel_previous_search();

                this._search_serial++;
                serial = this._search_serial;
                cancellable = new Gio.Cancellable();
                this._current_cancellable = cancellable;

                try {
                        display = await call_claude(prompt, cancellable);

                        if (cancellable.is_cancelled() || serial !== this._search_serial)
                                return;

                        this._cache.set(prompt, display);
                        this._trim_cache();

                        result_ids = [this._make_id(prompt)];
                        invocation.return_value(
                                new GLib.Variant('(as)', [result_ids])
                        );
                } catch (error) {
                        if (cancellable.is_cancelled() || serial !== this._search_serial)
                                return;

                        error_message = error.message || String(error);

                        this._cache.set(prompt, {
                                title: truncate_one_line(`Claude: ${prompt}`, 72),
                                description: truncate_one_line(`Error: ${error_message}`, 160),
                        });
                        this._trim_cache();

                        result_ids = [this._make_id(prompt)];
                        invocation.return_value(
                                new GLib.Variant('(as)', [result_ids])
                        );
                } finally {
                        if (this._current_cancellable === cancellable)
                                this._current_cancellable = null;
                }
        }

        _handle_get_initial_result_set(
                terms,
                invocation
        ) {
                let prompt;

                prompt = this._extract_prompt_from_terms(terms);
                if (!prompt) {
                        this._cancel_previous_search();
                        invocation.return_value(
                                new GLib.Variant('(as)', [[]])
                        );
                        return;
                }

                this._search_prompt(prompt, invocation);
        }

        _handle_get_subsearch_result_set(
                previous_results,
                terms,
                invocation
        ) {
                this._handle_get_initial_result_set(terms, invocation);
        }

        _handle_get_result_metas(
                identifiers,
                invocation
        ) {
                let metas;
                let id;
                let prompt;
                let display;

                metas = [];

                for (id of identifiers) {
                        prompt = this._prompt_from_id(id);

                        if (!prompt) {
                                metas.push(this._fallback_meta(
                                        id,
                                        null,
                                        'Invalid result'
                                ));
                                continue;
                        }

                        display = this._cache.get(prompt);
                        if (!display) {
                                metas.push(this._fallback_meta(
                                        id,
                                        prompt,
                                        'Not ready'
                                ));
                                continue;
                        }

                        metas.push(this._meta_from_display(id, display));
                }

                invocation.return_value(
                        new GLib.Variant('(aa{sv})', [metas])
                );
        }

        _handle_activate_result(
                identifier,
                terms,
                timestamp,
                invocation
        ) {
                invocation.return_value(null);
        }

        _handle_launch_search(
                terms,
                timestamp,
                invocation
        ) {
                invocation.return_value(null);
        }

        _handle_method_call(
                connection,
                sender,
                object_path,
                interface_name,
                method_name,
                parameters,
                invocation
        ) {
                let unpacked;
                let terms;
                let previous_results;
                let identifiers;
                let identifier;
                let timestamp;

                try {
                        unpacked = parameters.deepUnpack();

                        switch (method_name) {
                        case 'GetInitialResultSet':
                                [terms] = unpacked;
                                this._handle_get_initial_result_set(
                                        terms,
                                        invocation
                                );
                                return;

                        case 'GetSubsearchResultSet':
                                [previous_results, terms] = unpacked;
                                this._handle_get_subsearch_result_set(
                                        previous_results,
                                        terms,
                                        invocation
                                );
                                return;

                        case 'GetResultMetas':
                                [identifiers] = unpacked;
                                this._handle_get_result_metas(
                                        identifiers,
                                        invocation
                                );
                                return;

                        case 'ActivateResult':
                                [identifier, terms, timestamp] = unpacked;
                                this._handle_activate_result(
                                        identifier,
                                        terms,
                                        timestamp,
                                        invocation
                                );
                                return;

                        case 'LaunchSearch':
                                [terms, timestamp] = unpacked;
                                this._handle_launch_search(
                                        terms,
                                        timestamp,
                                        invocation
                                );
                                return;

                        default:
                                invocation.return_dbus_error(
                                        'com.example.ClaudeSearchProvider.Error',
                                        `Unknown method: ${method_name}`
                                );
                                return;
                        }
                } catch (error) {
                        invocation.return_dbus_error(
                                'com.example.ClaudeSearchProvider.Error',
                                error.message
                        );
                }
        }

}

function
main() {
let loop;
let provider;

        loop = new GLib.MainLoop(null, false);
        provider = new ClaudeSearchProvider();

        Gio.bus_own_name(
                Gio.BusType.SESSION,
                BUS_NAME,
                Gio.BusNameOwnerFlags.NONE,
                connection => {
                        provider.export(connection);
                },
                null,
                null
        );

        loop.run();

}

main();

ENVIRONMENT

The service needs access to ANTHROPIC_API_KEY.

At minimum, ensure the session that will activate the D-Bus service has:

ANTHROPIC_API_KEY=your-key-here

Optionally set:

CLAUDE_MODEL=claude-sonnet-4-6

VALIDATION COMMANDS

Check the exported D-Bus object:

gdbus introspect --session \
 --dest com.example.ClaudeSearchProvider \
 --object-path /com/example/ClaudeSearchProvider

Check that a query returns a result ID:

gdbus call --session \
 --dest com.example.ClaudeSearchProvider \
 --object-path /com/example/ClaudeSearchProvider \
 --method org.gnome.Shell.SearchProvider2.GetInitialResultSet \
 "['hello', 'world']"

Check that metadata is available for a result ID:

gdbus call --session \
 --dest com.example.ClaudeSearchProvider \
 --object-path /com/example/ClaudeSearchProvider \
 --method org.gnome.Shell.SearchProvider2.GetResultMetas \
 "['claude:hello%20world']"

Check the service on the user bus:

busctl --user list | grep Claude

Check the exported methods directly:

busctl --user introspect com.example.ClaudeSearchProvider /com/example/ClaudeSearchProvider

FINAL NOTES

This implementation is designed so that the search result only appears after Claude has answered. That produces the most reliable GNOME Shell behavior for this style of provider.

If you want the provider to react only for longer or more intentional queries, raise the minimum length check in \_extract_prompt_from_terms.

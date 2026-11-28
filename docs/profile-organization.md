# Profile tags and saved views

Tags are labels for organization. Creating or editing a profile accepts `tags: []`; an empty list clears the labels. Tags are trimmed, repeated whitespace is collapsed, Unicode is normalized, and spelling is case-folded (`Work` and `work` become the same label). Duplicates are removed. Each profile can have at most 20 tags, each 1–32 characters. Commas and control characters are not accepted.

The profile editor has a comma-separated Tags field. The profile list displays tag chips, offers group and tag filters, and can add or remove tags for a selection using **Edit tags**. Tag changes affect metadata only, so running profiles may be labeled safely. They preserve fingerprints, proxies, cookies, extension selections and running-browser leases. A batch accepts at most 1000 profiles and sends each selected row version. Changed, missing or trashed profiles are reported separately; successful profiles are kept. Removals happen before additions. Tags follow profile clones and profile archives.

## Filters and pagination

`GET /api/v1/profiles` filters before pagination:

- `search`: a literal name, ID or email substring, up to 255 characters. `%` and `_` are literal characters.
- `status`: `active`, `inactive`, `blocked` or `maintenance`.
- `group`: the group ID; `__ungrouped__` selects profiles without a group.
- `tag` or repeated `tags`: normalized tags, with every requested tag required. Up to 20 unique tags.
- `page`: a positive integer; `per_page`: 1–100.

Example: `/api/v1/profiles?tags=work&tags=client-a&group=team-a&status=active&per_page=25&page=2`.

The desktop list loads the complete collection before applying its local sort and pagination. It never silently filters only the first API page. An explicit limit of 20,000 loaded profiles produces an error if more profiles exist. Filter changes clear the previous selection and return to the first page. Page changes keep selections across pages. The browser URL carries search, group, status, repeated tags, sort, direction and page, so a valid filtered view survives reload. `sort` accepts `name`, `id`, `group`, `os`, `status` or `last_used`; `direction` is `asc` or `desc`. API resource pagination is independent of the desktop URL's client-side sort.

The list starts with the most recently created profile first. Clicking a column header sorts by that column, a second click reverses it, and a third returns to newest first. Profiles created in the same instant, as a batch creates them, stay in name order among themselves. A link carries `sort` and `direction` only when the order differs from newest first; a link made before this was the default, with `direction=desc` alone, still means names descending.

## Grouped list

The profile list has two layouts, chosen with **List** and **By group** next to the filters. **By group** is the default: every group folds into one line, and a click on the line opens or closes it. The line shows how many profiles the group holds, how many are running or blocked, how many have a proxy check that needs attention, and when one was last used. These numbers describe the whole group, whatever the filters hide. A group with a running profile carries the same orange marker as a running row, so it is visible while folded. Profiles without a group form the last section, **Ungrouped**.

Groups are ordered by name the way their numbers count, so `Homes 9` comes before `Homes 10`. Inside a group the rows follow the list's sort order. The Group column is left out, as every row would repeat the line above it. `sort=group` with `direction=desc` reverses the order of the groups.

The grouped layout has no pages, since a page boundary would cut a group in two. A folded group draws no rows, and an open one shows 50 at a time with **Show more**. The footer counts the groups and profiles that match.

A search, status filter or tag filter hides the groups without a match and shows `matched of total` on the others. While at most 200 profiles match, the matching groups open by themselves; folding one of them then holds only until the filters change. A list that comes down to a single section is always open. Otherwise the list starts folded, and **Expand all** and **Collapse all** act on every section at once.

The checkbox on a group line selects every matching profile of that group, folded or not, and shows a dash when only some are selected. The checkbox in the table header selects every matching profile in the list, where the flat list selects its page.

The layout and the open groups are remembered in the browser's local storage, per browser. They are not part of a saved view or of the URL, because saved views are shared by every user of the instance.

## Saved views

**Save view** stores search, status, group, tag filters and sort order in the manager's SQLite database. Saved views are instance-wide and shared by authorized users, like other manager resources; they are not personal browser-only preferences. Applying one starts on page one. Saved views can be renamed, updated to the current filters, or deleted without changing any profiles.

`GET/POST /api/v1/saved-filters` lists or creates views. `PATCH /api/v1/saved-filters/{id}` requires `row_version`, and deletion requires `?row_version=N`. Names are trimmed and limited to 100 characters. Configurations accept only the documented filter/sort fields; unknown fields and out-of-range values are rejected. Concurrent edits return `409 stale_write`; reload the saved view before making a deliberate new change.

A view that names a deleted group or unused tag can match zero profiles. Its filters remain visible and can be cleared or changed. Saved views do not copy profile data and are outside per-profile archive scope.

#include "dolly_player_producers.hpp"
#include <cstdlib>
#include <cstdio>
#include <thread>
#include <vector>

static void require(bool value) {
    if (!value)
        std::abort();
}
int main() {
    using namespace dolly::player_capture;
    ProducerTable<32> table;
    ProducerEntry selected{7, 5, 100, 1, 42, {1, 2, 3, 4, 5, 6, 7, 8}};
    table.store(selected);
    const auto saved = table.lookup(7, 5, 100);
    require(saved && saved->owner == 42);
    require(!table.lookup(8, 5, 100));
    require(!table.lookup(7, 5, 101));
    auto replaced = selected;
    replaced.owner = 0;
    table.store(replaced);
    require(table.lookup(7, 5, 100)->owner == 0);
    require(saved->owner == 42); // Returned values cannot mutate under readers.
    SubmittedOwners<2> submitted;
    auto revision = submitted.invalidate();
    require(submitted.publish(revision, 123, 100, &selected, 1));
    // Preparing the next generation cannot replace the already uploaded one.
    auto next = selected;
    next.frame = 8;
    next.owner = 77;
    table.store(next);
    require(submitted.lookup(123, 100, 5)->owner == 42);
    require(!submitted.lookup(124, 100, 5));
    require(!submitted.lookup(123, 101, 5));
    submitted.invalidate();
    require(!submitted.lookup(123, 100, 5));
    require(!submitted.publish(revision, 123, 100, &selected, 1));
    revision = submitted.invalidate();
    require(submitted.publish(revision, 123, 100, &next, 1));
    require(submitted.lookup(123, 100, 5)->owner == 77);
    revision = submitted.invalidate();
    require(!submitted.publish(revision, 123, 100, &next, 3));
    require(!submitted.lookup(123, 100, 5));
    require(record_matches(5, 5, selected.record, selected.record));
    require(!record_matches(6, 5, selected.record, selected.record));
    for (unsigned i = 0; i < 8; ++i) {
        auto changed = selected;
        ++changed.record[i];
        require(!record_matches(5, 5, changed.record, selected.record));
    }
    std::vector<std::thread> workers;
    for (unsigned worker = 0; worker < 11; ++worker)
        workers.emplace_back([&, worker] {
            for (unsigned n = 0; n < 2000; ++n) {
                ProducerEntry entry{n, worker, 100, n, worker + 1, {}};
                for (auto& word : entry.record)
                    word = n;
                table.store(entry);
                const auto read = table.lookup(n, worker, 100);
                if (read) {
                    require(read->owner == worker + 1 && read->event == n);
                    for (auto word : read->record)
                        require(word == n);
                }
            }
        });
    for (auto& worker : workers)
        worker.join();
    std::puts("Player producer tests passed");
}

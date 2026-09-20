#include "dolly_player_producers.hpp"
#include "dolly_capture_timing.hpp"
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
    using dolly::CaptureImageAdmission;
    using dolly::capture_image_admission;
    // Real Depth05 correction: every advancing rendered image survives even
    // though floor(phase*60) repeats 7 and jumps to9.
    double previous_phase = -1;
    for (double phase : {0., .016662598, .018737793, .035400391, .052062988, .068725586, .085418701,
                         .102081299, .118743896, .119781494, .152069092}) {
        require(capture_image_admission(previous_phase, phase, true) ==
                CaptureImageAdmission::capture);
        previous_phase = phase;
        require(capture_image_admission(previous_phase, phase, false) ==
                CaptureImageAdmission::skip);
        require(capture_image_admission(previous_phase, phase, true) ==
                CaptureImageAdmission::inconsistent);
    }
    require(capture_image_admission(previous_phase, .168731689, false) ==
            CaptureImageAdmission::missing);
    require(capture_image_admission(previous_phase, -1, true) ==
            CaptureImageAdmission::inconsistent);

    CaptureImageClock clock;
    require(!clock.accept(9, -1));
    require(!clock.accept(9, std::nan("")));
    require(clock.accept(9, .119781494));
    require(clock.accept(9, .119781494));
    require(!clock.accept(10, .119781494));
    require(!clock.accept(9, .152069092));
    require(clock.matches(9, .119781494));
    require(!clock.matches(9, .152069092));
    require(clock.phase == .119781494); // Failed checks never overwrite captured time.
    clock = CaptureImageClock{};
    require(clock.accept(10, .152069092));

    CaptureEventSlots<8> slots;
    auto pinned = slots.acquire();
    require(pinned.has_value());
    slots.retain(*pinned);
    slots.release(*pinned);
    for (unsigned i = 0; i < 100000; ++i) {
        const auto slot = slots.acquire();
        require(slot && *slot != *pinned);
        slots.release(*slot);
    }
    slots.release(*pinned);
    std::vector<unsigned> full;
    for (unsigned i = 0; i < 8; ++i) {
        const auto slot = slots.acquire();
        require(slot.has_value());
        full.push_back(*slot);
    }
    require(!slots.acquire());
    for (const auto slot : full)
        slots.release(slot);
    slots.reset();
    require(slots.acquire().has_value());
    // Exercise the lifetime contract under concurrent producers. Releasing one
    // reference must not make a still-pinned event available to another writer.
    CaptureEventSlots<8> concurrent;
    std::array<std::atomic<unsigned>, 8> owners{};
    std::vector<std::thread> slot_workers;
    std::atomic<unsigned> acquisitions{0};
    for (unsigned worker = 0; worker < 4; ++worker)
        slot_workers.emplace_back([&, worker] {
            for (unsigned n = 0; n < 10000; ++n) {
                const auto slot = concurrent.acquire();
                // A bounded scan may lose every CAS to concurrent churn even
                // when capacity exists; callers may retry on a later callback.
                if (!slot)
                    continue;
                acquisitions.fetch_add(1);
                unsigned free = 0;
                require(owners[*slot].compare_exchange_strong(free, worker + 1));
                concurrent.retain(*slot);
                concurrent.release(*slot);
                if (n % 31 == 0)
                    std::this_thread::yield();
                require(owners[*slot].load() == worker + 1);
                owners[*slot].store(0);
                concurrent.release(*slot);
            }
        });
    for (auto& worker : slot_workers)
        worker.join();
    require(acquisitions.load() > 0);
    // Every reference must have been released after all producers finish.
    for (unsigned n = 0; n < 8; ++n)
        require(concurrent.acquire().has_value());
    require(!concurrent.acquire());
    for (unsigned fps : {30u, 60u, 120u, 300u, 600u})
        for (unsigned n = 0; n < 6000; ++n)
            require(capture_frame_index(double(n) / fps, fps) == n);
    for (unsigned fps : {30u, 60u, 120u, 300u, 600u})
        for (double speed : {.05, .5, 1., 2., 4.})
            for (unsigned n = 0; n < 6000; ++n)
                require(capture_frame_index(double(n) * speed / fps, fps / speed) == n);
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
    table.clear();
    require(table.snapshot().count == 0);
    std::puts("Player producer tests passed");
}

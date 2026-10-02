"use client";

import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatNumber } from "@/lib/format";
import type { ListState } from "@/lib/url-state";
import { cn } from "@/lib/utils";

const PER_PAGE = [10, 25, 50];

type DataTableProps<T> = {
  columns: ColumnDef<T>[];
  data: T[];
  rowCount: number;
  state: ListState;
  onStateChange: (changes: Partial<ListState>) => void;
  isLoading: boolean;
  isFetching: boolean;
  /** Estado vazio de verdade (sem filtros) e estado vazio com filtro, ambos prontos para exibir. */
  emptyState: React.ReactNode;
  filteredEmptyState?: React.ReactNode;
  hasFilters?: boolean;
  /** Erro sem dados na tela: um EmptyState de erro. */
  errorState?: React.ReactNode;
  getRowId: (row: T) => string;
  /** Abaixo de 900px a tabela vira lista de cards. */
  mobileCard: (row: T) => React.ReactNode;
};

function toSorting(sort: string | null): SortingState {
  if (!sort) return [];
  return [{ id: sort.replace(/^-/, ""), desc: sort.startsWith("-") }];
}

/**
 * DataTable (design-system.md §5): ordenação, filtro e paginação manuais (no servidor), estado na URL.
 * Cabeçalho de 44px, linha de 56px, rodapé com "Linhas por página" e "Anterior"/"Próxima".
 */
export function DataTable<T>({
  columns,
  data,
  rowCount,
  state,
  onStateChange,
  isLoading,
  isFetching,
  emptyState,
  filteredEmptyState,
  hasFilters = false,
  errorState,
  getRowId,
  mobileCard,
}: Readonly<DataTableProps<T>>) {
  // TanStack Table devolve funções que o React Compiler não consegue memoizar; este componente
  // simplesmente fica fora da memoização automática.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data,
    columns,
    getRowId,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    manualPagination: true,
    manualFiltering: true,
    rowCount,
    state: { sorting: toSorting(state.sort) },
    onSortingChange: (updater) => {
      const next = typeof updater === "function" ? updater(toSorting(state.sort)) : updater;
      const first = next[0];
      onStateChange({ sort: first ? `${first.desc ? "-" : ""}${first.id}` : null });
    },
  });

  if (errorState && !isLoading && data.length === 0) return <>{errorState}</>;
  if (!isLoading && data.length === 0) {
    return <>{hasFilters && filteredEmptyState ? filteredEmptyState : emptyState}</>;
  }

  const first = rowCount === 0 ? 0 : (state.page - 1) * state.perPage + 1;
  const last = Math.min(rowCount, state.page * state.perPage);
  const pageCount = Math.max(1, Math.ceil(rowCount / state.perPage));

  return (
    <div className="grid min-w-0 grid-cols-1 gap-3">
      {/* Desktop */}
      <div className={cn("min-w-0 max-[899px]:hidden", isFetching && !isLoading && "opacity-50")}>
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id} className="h-11 hover:bg-transparent">
                {group.headers.map((header) => {
                  const sorted = header.column.getIsSorted();
                  const canSort = header.column.getCanSort();
                  return (
                    <TableHead
                      key={header.id}
                      aria-sort={
                        sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined
                      }
                      className={(header.column.columnDef.meta as { className?: string } | undefined)?.className}
                    >
                      {header.isPlaceholder ? null : canSort ? (
                        <button
                          type="button"
                          onClick={header.column.getToggleSortingHandler()}
                          className="flex min-h-11 items-center gap-1 uppercase hover:text-text"
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {sorted === "asc" ? (
                            <ArrowUp aria-hidden className="size-3.5" />
                          ) : sorted === "desc" ? (
                            <ArrowDown aria-hidden className="size-3.5" />
                          ) : (
                            <ChevronsUpDown aria-hidden className="size-3.5 opacity-60" />
                          )}
                        </button>
                      ) : (
                        flexRender(header.column.columnDef.header, header.getContext())
                      )}
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {isLoading
              ? Array.from({ length: Math.min(state.perPage, 5) }, (_, index) => (
                  <TableRow key={index}>
                    {columns.map((_column, cell) => (
                      <TableCell key={cell}>
                        <Skeleton className="h-5 w-3/4" />
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              : table.getRowModel().rows.map((row) => (
                  <TableRow key={row.id}>
                    {row.getVisibleCells().map((cell) => (
                      <TableCell
                        key={cell.id}
                        className={(cell.column.columnDef.meta as { className?: string } | undefined)?.className}
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
          </TableBody>
        </Table>
      </div>

      {/* Mobile: lista de cards */}
      <ul className={cn("grid gap-3 min-[900px]:hidden", isFetching && !isLoading && "opacity-50")}>
        {isLoading
          ? Array.from({ length: 3 }, (_, index) => (
              <li key={index}>
                <Skeleton className="h-24" />
              </li>
            ))
          : data.map((row) => (
              <li
                key={getRowId(row)}
                className="rounded-card border border-border bg-panel p-4"
              >
                {mobileCard(row)}
              </li>
            ))}
      </ul>

      <div className="flex flex-wrap items-center justify-between gap-3 text-body-sm text-text-label">
        <label className="flex items-center gap-2">
          Linhas por página
          <select
            value={state.perPage}
            onChange={(event) => onStateChange({ perPage: Number(event.target.value) })}
            className="h-11 rounded-control border border-border-control bg-sidebar px-2 text-body text-text"
          >
            {PER_PAGE.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </label>
        <p className="tabular">
          {formatNumber(first)}–{formatNumber(last)} de {formatNumber(rowCount)}
        </p>
        <nav aria-label="Paginação" className="flex items-center gap-2">
          <Button
            variant="secondary"
            disabled={state.page <= 1}
            onClick={() => onStateChange({ page: state.page - 1 })}
          >
            Anterior
          </Button>
          <span className="px-1 tabular text-text-secondary">
            {state.page} / {pageCount}
          </span>
          <Button
            variant="secondary"
            disabled={state.page >= pageCount}
            onClick={() => onStateChange({ page: state.page + 1 })}
          >
            Próxima
          </Button>
        </nav>
      </div>
    </div>
  );
}
